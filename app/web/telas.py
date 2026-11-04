"""Rotas das páginas (HTML). A API JSON continua em /documentos, /perguntas e /saude."""

import re
from pathlib import Path
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session, get_sessionmaker
from app.ingestao import remocao, servico
from app.ingestao.config import IngestaoSettings, get_ingestao_settings
from app.ingestao.extracao import EXTENSOES_ACEITAS
from app.llm.protocolos import GeradorDeEmbeddings
from app.routers import documentos as api_documentos
from app.web import formato, sessao

RAIZ = Path(__file__).parent
templates = Jinja2Templates(directory=str(RAIZ / "templates"))
templates.env.filters.update(
    data_br=formato.data_br,
    numero=formato.numero,
    estado=formato.estado,
    tipo=formato.tipo,
    resumo=formato.resumo,
    citado=formato.texto_citado,
)

router = APIRouter(prefix="/app", include_in_schema=False)
protegido = APIRouter(
    prefix="/app", include_in_schema=False, dependencies=[Depends(sessao.exigir_sessao)]
)
COLECAO_VALIDA = re.compile(r"[\w.-]+")
acao = [Depends(sessao.exigir_htmx)]  # ações que alteram dados

Sessao = Annotated[Session, Depends(get_session)]
Estado = Literal["pendente", "processado", "falhou"]


def estado_do_filtro(estado: Annotated[str, Query(max_length=12)] = "") -> Estado | None:
    """Filtro de estado da URL. Vazio (o "todos" do formulário) não filtra; inválido dá 422."""
    if not estado:
        return None
    if estado not in ("pendente", "processado", "falhou"):
        raise HTTPException(422, "Estado inválido")
    return estado  # type: ignore[return-value]


FiltroDeEstado = Annotated[Estado | None, Depends(estado_do_filtro)]


def pagina(request: Request, nome: str, **contexto: object) -> HTMLResponse:
    return templates.TemplateResponse(request, nome, contexto)


# --- entrar e sair -----------------------------------------------------------------------------


@router.get("/entrar", response_class=HTMLResponse)
def tela_de_entrada(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> HTMLResponse:
    return pagina(request, "entrar.html", erro=None, sem_chave=sessao.gerar_token(settings) is None)


@router.post("/entrar", response_model=None)
def entrar(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    chave: Annotated[str, Form()] = "",
) -> Response:
    if not sessao.chave_confere(chave, settings):
        sem_chave = sessao.gerar_token(settings) is None
        resposta = pagina(request, "entrar.html", erro=True, sem_chave=sem_chave)
        resposta.status_code = 401
        return resposta
    resposta = RedirectResponse("/app/documentos", status_code=303)
    resposta.set_cookie(
        sessao.NOME_DO_COOKIE,
        sessao.gerar_token(settings) or "",
        max_age=sessao.DURACAO_S,
        httponly=True,
        samesite="strict",
        secure=settings.em_producao,
        path="/app",
    )
    return resposta


@router.post("/sair")
def sair() -> RedirectResponse:
    resposta = RedirectResponse(sessao.ENTRAR, status_code=303)
    resposta.delete_cookie(sessao.NOME_DO_COOKIE, path="/app")
    return resposta


# --- documentos --------------------------------------------------------------------------------


def _lista(session: Session, colecao: str | None, estado: Estado | None) -> dict[str, object]:
    return api_documentos.listar_documentos(
        session, colecao=colecao or None, estado=estado, limite=200, deslocamento=0
    )


def _colecoes(session: Session) -> list[str]:
    return list(
        session.execute(text("SELECT DISTINCT colecao FROM documento ORDER BY colecao")).scalars()
    )


def _contexto_da_tabela(
    session: Session, colecao: str | None, estado: Estado | None
) -> dict[str, object]:
    lista = _lista(session, colecao, estado)
    itens = lista["itens"]
    return {
        "total": lista["total"],
        "itens": itens,
        "colecao": colecao or "",
        "estado": estado or "",
        "pendentes": any(i["estado"] == "pendente" for i in itens),  # type: ignore[union-attr]
    }


@protegido.get("/documentos", response_class=HTMLResponse)
def tela_de_documentos(
    request: Request,
    session: Sessao,
    config: Annotated[IngestaoSettings, Depends(get_ingestao_settings)],
    colecao: Annotated[str | None, Query(max_length=64)] = None,
    estado: FiltroDeEstado = None,
    removido: Literal["0", "1"] | None = None,
) -> HTMLResponse:
    return pagina(
        request,
        "documentos.html",
        ativa="documentos",
        removido=removido,
        colecoes=_colecoes(session),
        limite_mb=config.tamanho_max_mb,
        extensoes=sorted(EXTENSOES_ACEITAS),
        **_contexto_da_tabela(session, colecao, estado),
    )


@protegido.get("/documentos/tabela", response_class=HTMLResponse)
def tabela_de_documentos(
    request: Request,
    session: Sessao,
    colecao: Annotated[str | None, Query(max_length=64)] = None,
    estado: FiltroDeEstado = None,
) -> HTMLResponse:
    return pagina(request, "parciais/tabela.html", **_contexto_da_tabela(session, colecao, estado))


SITUACAO = {
    "novo": "enviado, processando",
    "duplicado": "já estava na coleção, nada foi duplicado",
    "nova_versao": "nova versão, substituindo a anterior",
}


@protegido.post("/documentos/enviar", response_class=HTMLResponse, dependencies=acao)
def enviar(
    request: Request,
    background: BackgroundTasks,
    session: Sessao,
    config: Annotated[IngestaoSettings, Depends(get_ingestao_settings)],
    embeddings: Annotated[GeradorDeEmbeddings, Depends(api_documentos.obter_embeddings)],
    arquivos: Annotated[list[UploadFile], File()],
    colecao: Annotated[str, Form(max_length=64)] = "geral",
) -> HTMLResponse:
    colecao = colecao.strip() or "geral"
    resultados: list[dict[str, str]] = []
    for arquivo in arquivos:
        nome = arquivo.filename or "(sem nome)"
        try:
            if not COLECAO_VALIDA.fullmatch(colecao):
                raise servico.ArquivoInvalido(
                    "O nome da coleção só aceita letras, números, ponto, hífen e sublinhado"
                )
            dados = arquivo.file.read(config.tamanho_max_bytes + 1)
            if len(dados) > config.tamanho_max_bytes:
                raise servico.ArquivoInvalido(f"Arquivo maior que {config.tamanho_max_mb} MB")
            registro = servico.registrar(
                session, nome=nome, conteudo=dados, colecao=colecao, config=config
            )
        except servico.ArquivoInvalido as erro:
            resultados.append({"nome": nome, "ok": "", "mensagem": str(erro)})
            continue
        if registro.precisa_processar:
            background.add_task(
                servico.processar, get_sessionmaker(), registro.documento_id, embeddings, config
            )
        resultados.append(
            {
                "nome": nome,
                "ok": "1",
                "mensagem": SITUACAO.get(registro.situacao, registro.situacao),
            }
        )
    resposta = pagina(
        request,
        "parciais/envio.html",
        resultados=resultados,
        colecoes=_colecoes(session),
        **_contexto_da_tabela(session, None, None),
    )
    return resposta


def _resultado_do_lote(
    request: Request, session: Session, avisos: list[tuple[bool, str]], colecao: str, estado: str
) -> HTMLResponse:
    filtro = estado if estado in ("pendente", "processado", "falhou") else None
    return pagina(
        request,
        "parciais/lote.html",
        avisos=avisos,
        colecoes=_colecoes(session),
        **_contexto_da_tabela(session, colecao or None, filtro),
    )


@protegido.post("/documentos/reprocessar-lote", response_class=HTMLResponse, dependencies=acao)
def reprocessar_lote(
    request: Request,
    background: BackgroundTasks,
    session: Sessao,
    config: Annotated[IngestaoSettings, Depends(get_ingestao_settings)],
    embeddings: Annotated[GeradorDeEmbeddings, Depends(api_documentos.obter_embeddings)],
    ids: Annotated[list[int], Form()] = [],  # noqa: B006
    colecao: Annotated[str, Form(max_length=64)] = "",
    estado: Annotated[str, Form(max_length=12)] = "",
) -> HTMLResponse:
    if not ids:
        avisos = [(False, "Marque ao menos um documento.")]
    else:
        feitos = servico.marcar_varios_para_reprocessar(session, ids[:500])
        if feitos:
            background.add_task(
                servico.processar_fila, get_sessionmaker(), feitos, embeddings, config
            )
        plural = "" if len(feitos) == 1 else "s"
        avisos = [(True, f"{len(feitos)} documento{plural} na fila, um de cada vez.")]
    return _resultado_do_lote(request, session, avisos, colecao, estado)


@protegido.post("/documentos/mover-lote", response_class=HTMLResponse, dependencies=acao)
def mover_lote(
    request: Request,
    session: Sessao,
    ids: Annotated[list[int], Form()] = [],  # noqa: B006
    destino: Annotated[str, Form(max_length=64)] = "",
    colecao: Annotated[str, Form(max_length=64)] = "",
    estado: Annotated[str, Form(max_length=12)] = "",
) -> HTMLResponse:
    destino = destino.strip()
    if not ids:
        avisos = [(False, "Marque ao menos um documento.")]
    elif not COLECAO_VALIDA.fullmatch(destino):
        avisos = [(False, "Dê a coleção de destino: letras, números, ponto, hífen e sublinhado.")]
    else:
        r = servico.mover_para_colecao(session, ids[:500], destino, ator="interface")
        avisos = []
        if r.movidos:
            plural = "" if len(r.movidos) == 1 else "s"
            avisos.append(
                (True, f"{len(r.movidos)} documento{plural} movido{plural} para “{destino}”.")
            )
        avisos += [(False, f"{nome}: {motivo}") for _, nome, motivo in r.ignorados]
    return _resultado_do_lote(request, session, avisos, colecao, estado)


@protegido.post("/documentos/{documento_id}/mover", response_class=HTMLResponse, dependencies=acao)
def mover_um(
    documento_id: int, session: Sessao, destino: Annotated[str, Form(max_length=64)] = ""
) -> Response:
    destino = destino.strip()
    if not COLECAO_VALIDA.fullmatch(destino):
        return HTMLResponse(
            '<p class="aviso erro" role="alert">Coleção inválida: use letras, números, ponto, '
            "hífen e sublinhado.</p>"
        )
    r = servico.mover_para_colecao(session, [documento_id], destino, ator="interface")
    if r.ignorados:
        return HTMLResponse(
            f'<p class="aviso erro" role="alert">{r.ignorados[0][2].capitalize()}.</p>'
        )
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@protegido.get("/documentos/{documento_id}", response_class=HTMLResponse)
def tela_do_documento(request: Request, documento_id: int, session: Sessao) -> HTMLResponse:
    try:
        documento = api_documentos.ver_documento(documento_id, session, amostra=20)
        secoes = api_documentos.ver_secoes(documento_id, session)
    except HTTPException:
        return pagina(request, "nao_encontrado.html", ativa="documentos", o_que="documento")
    tipos = documento["tipos"]
    maior = max(tipos.values(), default=1)  # type: ignore[union-attr]
    return pagina(
        request,
        "documento.html",
        ativa="documentos",
        d=documento,
        colecoes=_colecoes(session),
        secoes=secoes["secoes"],
        sem_secao=secoes["trechos_sem_secao"],
        maior=maior,
    )


@protegido.get("/documentos/{documento_id}/estado", response_class=HTMLResponse)
def estado_do_documento(
    request: Request, documento_id: int, session: Sessao, era: str = ""
) -> HTMLResponse:
    try:
        documento = api_documentos.ver_documento(documento_id, session)
    except HTTPException:
        return pagina(request, "nao_encontrado.html", ativa="documentos", o_que="documento")
    resposta = pagina(request, "parciais/estado.html", d=documento)
    if era == "pendente" and documento["estado"] != "pendente":
        resposta.headers["HX-Refresh"] = "true"  # terminou: recarrega para mostrar os números
    return resposta


@protegido.post(
    "/documentos/{documento_id}/reprocessar", response_class=HTMLResponse, dependencies=acao
)
def reprocessar(
    request: Request,
    documento_id: int,
    background: BackgroundTasks,
    session: Sessao,
    config: Annotated[IngestaoSettings, Depends(get_ingestao_settings)],
    embeddings: Annotated[GeradorDeEmbeddings, Depends(api_documentos.obter_embeddings)],
) -> HTMLResponse:
    if not servico.marcar_para_reprocessar(session, documento_id):
        return pagina(request, "nao_encontrado.html", ativa="documentos", o_que="documento")
    background.add_task(servico.processar, get_sessionmaker(), documento_id, embeddings, config)
    documento = api_documentos.ver_documento(documento_id, session)
    return pagina(request, "parciais/estado.html", d=documento)


@protegido.delete("/documentos/{documento_id}", dependencies=acao)
def remover(
    documento_id: int,
    session: Sessao,
    config: Annotated[IngestaoSettings, Depends(get_ingestao_settings)],
    consultas: Literal["redigir", "apagar"] = "redigir",
) -> Response:
    resultado = remocao.remover(session, documento_id, consultas=consultas, config=config)
    destino = "/app/documentos?removido=" + ("1" if resultado else "0")
    return Response(status_code=200, headers={"HX-Redirect": destino})
