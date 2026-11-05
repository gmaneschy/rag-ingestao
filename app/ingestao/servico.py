"""Registro e processamento de documentos.

Dois passos separados, porque o envio precisa responder rápido e a ingestão é lenta:
  registrar()  grava o arquivo e uma linha em `documento` (estado 'pendente');
  processar()  extrai, divide, gera os vetores e troca os trechos em uma única transação.
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.ingestao.classificacao import classificar
from app.ingestao.config import IngestaoSettings
from app.ingestao.divisao import dividir
from app.ingestao.extracao import (
    EXTENSOES_ACEITAS,
    ErroDeExtracao,
    conferir_assinatura,
    extensao_do_nome,
    extrair,
)
from app.llm.protocolos import GeradorDeEmbeddings
from app.llm.tipos import DIMENSAO_VETOR, ModeloIndisponivel

logger = logging.getLogger("app.ingestao")

Situacao = Literal["novo", "nova_versao", "duplicado"]

_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")


class ArquivoInvalido(ValueError):
    """Nome, tipo ou conteúdo recusado na entrada."""


@dataclass(frozen=True)
class Registro:
    documento_id: int
    situacao: Situacao
    estado: str

    @property
    def precisa_processar(self) -> bool:
        # Duplicado já processado não faz nada; pendente ou falho tenta de novo (idempotente).
        return self.estado in {"pendente", "falhou"}


def limpar_nome(nome: str) -> str:
    """Só o nome do arquivo (sem diretórios), sem caracteres de controle."""
    nome = _CONTROLE.sub("", nome.replace("\\", "/").split("/")[-1]).strip()
    if not nome or nome in {".", ".."} or len(nome) > 255:
        raise ArquivoInvalido("Nome de arquivo inválido")
    if extensao_do_nome(nome) not in EXTENSOES_ACEITAS:
        raise ArquivoInvalido(
            f"Tipo não suportado; aceitos: {', '.join(sorted(EXTENSOES_ACEITAS))}"
        )
    return nome


def _vetor_como_texto(vetor: list[float]) -> str:
    return "[" + ",".join(f"{v:.8g}" for v in vetor) + "]"


def _remover_arquivo(caminho: str | None) -> None:
    if not caminho:
        return
    try:
        Path(caminho).unlink(missing_ok=True)
    except OSError:
        logger.warning("não consegui apagar arquivo antigo", extra={"caminho": caminho})


def registrar(
    session: Session, *, nome: str, conteudo: bytes, colecao: str, config: IngestaoSettings
) -> Registro:
    """Grava o arquivo e o documento. Idempotente: o mesmo conteúdo não entra duas vezes."""
    nome = limpar_nome(nome)
    if not conteudo:
        raise ArquivoInvalido("Arquivo vazio")
    if len(conteudo) > config.tamanho_max_bytes:
        raise ArquivoInvalido(f"Arquivo maior que {config.tamanho_max_mb} MB")
    try:
        conferir_assinatura(nome, conteudo)
    except ValueError as erro:
        raise ArquivoInvalido(str(erro)) from erro

    hash_conteudo = hashlib.sha256(conteudo).hexdigest()
    try:
        return _registrar(session, nome, conteudo, colecao, hash_conteudo, config)
    except IntegrityError:
        # Dois envios idênticos ao mesmo tempo: o segundo bate na restrição de unicidade.
        session.rollback()
        existente = _buscar_por_hash(session, colecao, hash_conteudo)
        if existente is None:
            raise
        return Registro(existente[0], "duplicado", existente[1])


def _buscar_por_hash(session: Session, colecao: str, hash_conteudo: str):
    return session.execute(
        text("SELECT id, estado FROM documento WHERE colecao = :c AND hash_conteudo = :h"),
        {"c": colecao, "h": hash_conteudo},
    ).first()


def _registrar(
    session: Session,
    nome: str,
    conteudo: bytes,
    colecao: str,
    hash_conteudo: str,
    config: IngestaoSettings,
) -> Registro:
    duplicado = _buscar_por_hash(session, colecao, hash_conteudo)
    if duplicado:
        documento_id, estado = duplicado
        if estado == "falhou":
            # Nova tentativa: volta a 'pendente' já aqui, para quem acompanha o estado (o script,
            # o n8n, a tela) não ler o erro velho e achar que a nova tentativa também falhou.
            session.execute(
                text(
                    "UPDATE documento SET estado = 'pendente', mensagem_erro = NULL, "
                    f"{_SEM_PROGRESSO}, atualizado_em = now() WHERE id = :id"
                ),
                {"id": documento_id},
            )
            session.commit()
            estado = "pendente"
        else:
            session.rollback()
        return Registro(documento_id, "duplicado", estado)

    anterior = session.execute(
        text("SELECT id, versao, caminho_arquivo FROM documento WHERE colecao = :c AND nome = :n"),
        {"c": colecao, "n": nome},
    ).first()

    if anterior:
        documento_id, versao = anterior[0], anterior[1] + 1
        situacao: Situacao = "nova_versao"
        session.execute(
            text(
                "UPDATE documento SET hash_conteudo = :h, versao = :v, estado = 'pendente', "
                f"mensagem_erro = NULL, {_SEM_PROGRESSO}, atualizado_em = now() WHERE id = :id"
            ),
            {"h": hash_conteudo, "v": versao, "id": documento_id},
        )
    else:
        versao = 1
        situacao = "novo"
        documento_id = session.execute(
            text(
                "INSERT INTO documento (nome, hash_conteudo, colecao, caminho_arquivo) "
                "VALUES (:n, :h, :c, '') RETURNING id"
            ),
            {"n": nome, "h": hash_conteudo, "c": colecao},
        ).scalar_one()

    destino = config.arquivos_dir / str(documento_id) / f"v{versao}{extensao_do_nome(nome)}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(conteudo)
    session.execute(
        text("UPDATE documento SET caminho_arquivo = :p WHERE id = :id"),
        {"p": str(destino), "id": documento_id},
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        _remover_arquivo(str(destino))
        raise

    if anterior:  # a versão antiga do arquivo só sai depois que a nova está gravada
        _remover_arquivo(anterior[2])
    return Registro(documento_id, situacao, "pendente")


_SEM_PROGRESSO = "progresso = NULL, etapa = NULL, processando_desde = NULL"

# Como o tempo se divide: ler e dividir é pouco; gerar os vetores é o grosso (cerca de 3 a 5
# trechos por segundo na CPU); gravar é rápido. Os percentuais seguem essa proporção.
_PCT_DEPOIS_DE_LER = 10
_PCT_FIM_DOS_VETORES = 95
_LOTE_DE_VETORES = 16


def _progresso(
    fabrica: sessionmaker[Session], documento_id: int, pct: int, etapa: str, inicio: bool = False
) -> None:
    """Grava o andamento numa sessão própria. Não mexe em atualizado_em (a lista é ordenada por
    ele) e nunca derruba a ingestão: um erro aqui só deixa o número parado."""
    try:
        with fabrica() as session:
            session.execute(
                text(
                    "UPDATE documento SET progresso = :p, etapa = :e, "
                    "processando_desde = CASE WHEN :ini THEN now() ELSE processando_desde END "
                    "WHERE id = :id"
                ),
                {"p": max(0, min(100, pct)), "e": etapa, "ini": inicio, "id": documento_id},
            )
            session.commit()
    except Exception:
        logger.warning("não consegui gravar o progresso", extra={"documento_id": documento_id})


def _vetores_com_progresso(
    fabrica: sessionmaker[Session],
    documento_id: int,
    embeddings: GeradorDeEmbeddings,
    textos: list[str],
) -> list[list[float]]:
    total = len(textos)
    vetores: list[list[float]] = []
    for i in range(0, total, _LOTE_DE_VETORES):
        vetores += embeddings.gerar_trechos(textos[i : i + _LOTE_DE_VETORES])
        feitos = len(vetores)
        pct = _PCT_DEPOIS_DE_LER + (_PCT_FIM_DOS_VETORES - _PCT_DEPOIS_DE_LER) * feitos // total
        _progresso(fabrica, documento_id, pct, f"Gerando vetores ({feitos} de {total} trechos)")
    return vetores


def marcar_para_reprocessar(session: Session, documento_id: int) -> bool:
    """Volta o documento a 'pendente' (os trechos atuais seguem consultáveis até a troca)."""
    achou = session.execute(
        text(
            "UPDATE documento SET estado = 'pendente', mensagem_erro = NULL, "
            f"{_SEM_PROGRESSO}, atualizado_em = now() WHERE id = :id RETURNING id"
        ),
        {"id": documento_id},
    ).first()
    session.commit()
    return achou is not None


def marcar_varios_para_reprocessar(session: Session, ids: list[int]) -> list[int]:
    """Volta os documentos a 'pendente'. Devolve os ids que existem, na ordem pedida."""
    ordem = list(dict.fromkeys(ids))
    achados = set(
        session.execute(
            text(
                "UPDATE documento SET estado = 'pendente', mensagem_erro = NULL, "
                f"{_SEM_PROGRESSO}, atualizado_em = now() WHERE id = ANY(:ids) RETURNING id"
            ),
            {"ids": ordem},
        ).scalars()
    )
    session.commit()
    return [i for i in ordem if i in achados]


def processar_fila(
    fabrica: sessionmaker[Session],
    ids: list[int],
    embeddings: GeradorDeEmbeddings,
    config: IngestaoSettings,
) -> None:
    """Processa um documento por vez: em lote, não se carrega o modelo N vezes em paralelo."""
    for documento_id in ids:
        processar(fabrica, documento_id, embeddings, config)


@dataclass(frozen=True)
class Movimentacao:
    movidos: list[int]
    ignorados: list[tuple[int, str, str]]  # (id, nome, motivo)


def mover_para_colecao(
    session: Session, ids: list[int], destino: str, *, ator: str = "api"
) -> Movimentacao:
    """Troca a coleção dos documentos, sem reindexar: o trecho não guarda a coleção.

    Recusa, documento a documento, o que colidiria no destino (mesmo nome ou mesmo conteúdo)."""
    movidos: list[int] = []
    ignorados: list[tuple[int, str, str]] = []
    for documento_id in dict.fromkeys(ids):
        linha = session.execute(
            text("SELECT nome, colecao, hash_conteudo FROM documento WHERE id = :id FOR UPDATE"),
            {"id": documento_id},
        ).first()
        if linha is None:
            ignorados.append((documento_id, f"#{documento_id}", "não existe mais"))
            continue
        nome, atual, hash_conteudo = linha
        if atual == destino:
            ignorados.append((documento_id, nome, "já está nessa coleção"))
            continue
        colide = session.execute(
            text(
                "SELECT nome = :n AS mesmo_nome FROM documento "
                "WHERE colecao = :c AND (nome = :n OR hash_conteudo = :h) ORDER BY 1 DESC LIMIT 1"
            ),
            {"c": destino, "n": nome, "h": hash_conteudo},
        ).first()
        if colide is not None:
            motivo = (
                "já existe um documento com esse nome no destino"
                if colide[0]
                else "o destino já tem um documento com o mesmo conteúdo"
            )
            ignorados.append((documento_id, nome, motivo))
            continue
        session.execute(
            text("UPDATE documento SET colecao = :c, atualizado_em = now() WHERE id = :id"),
            {"c": destino, "id": documento_id},
        )
        session.execute(
            text(
                "INSERT INTO evento_documento (documento_id, nome, tipo, ator) "
                "VALUES (:d, :n, 'movido', :a)"
            ),
            {"d": documento_id, "n": nome, "a": ator},
        )
        movidos.append(documento_id)
    session.commit()
    return Movimentacao(movidos, ignorados)


def processar(
    fabrica: sessionmaker[Session],
    documento_id: int,
    embeddings: GeradorDeEmbeddings,
    config: IngestaoSettings,
) -> None:
    """Ingere o documento. Nunca levanta: o resultado fica em documento.estado."""
    inicio = time.perf_counter()
    try:
        with fabrica() as session:
            linha = session.execute(
                text("SELECT hash_conteudo, caminho_arquivo, versao FROM documento WHERE id = :id"),
                {"id": documento_id},
            ).first()
        if linha is None:
            return  # removido antes de processar
        hash_lido, caminho, _versao = linha

        if embeddings.dimensao != DIMENSAO_VETOR:
            raise ErroDeExtracao(
                f"O modelo gera {embeddings.dimensao} dimensões; a coluna aceita {DIMENSAO_VETOR}"
            )
        _progresso(fabrica, documento_id, 1, "Lendo o arquivo", inicio=True)
        extracao = extrair(Path(caminho), max_paginas=config.max_paginas)
        _progresso(fabrica, documento_id, 6, "Dividindo em trechos")
        trechos = classificar(
            dividir(extracao.paginas, config.tamanho_trecho, config.sobreposicao_trecho)
        )
        _progresso(
            fabrica,
            documento_id,
            _PCT_DEPOIS_DE_LER,
            f"Gerando vetores (0 de {len(trechos)} trechos)",
        )
        vetores = _vetores_com_progresso(
            fabrica, documento_id, embeddings, [t.texto for t in trechos]
        )
        _progresso(fabrica, documento_id, 97, "Gravando")
        if len(vetores) != len(trechos):
            raise ErroDeExtracao("O gerador de embeddings devolveu uma quantidade inesperada")

        with fabrica() as session:
            atual = session.execute(
                text("SELECT hash_conteudo FROM documento WHERE id = :id FOR UPDATE"),
                {"id": documento_id},
            ).scalar()
            if atual != hash_lido:
                return  # chegou uma versão mais nova (ou o documento sumiu): ela é quem processa
            # A troca inteira é uma transação: a busca vê o texto antigo ou o novo, nunca metade.
            session.execute(
                text("DELETE FROM trecho WHERE documento_id = :id"), {"id": documento_id}
            )
            session.execute(
                text(
                    "INSERT INTO trecho (documento_id, posicao, pagina, secao, tipo, texto, vetor, "
                    "modelo_embedding, dimensao) VALUES (:d, :pos, :pag, :sec, :tipo, :t, "
                    "CAST(:v AS vector), :m, :dim)"
                ),
                [
                    {
                        "d": documento_id,
                        "pos": t.posicao,
                        "pag": t.pagina,
                        "sec": t.secao,
                        "tipo": t.tipo,
                        "t": t.texto,
                        "v": _vetor_como_texto(v),
                        "m": embeddings.nome_modelo,
                        "dim": embeddings.dimensao,
                    }
                    for t, v in zip(trechos, vetores, strict=True)
                ],
            )
            session.execute(
                text(
                    "UPDATE documento SET estado = 'processado', mensagem_erro = NULL, "
                    f"aviso = :aviso, {_SEM_PROGRESSO}, atualizado_em = now() WHERE id = :id"
                ),
                {"id": documento_id, "aviso": extracao.aviso},
            )
            ja_ingerido = session.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM evento_documento "
                    "WHERE documento_id = :id AND tipo = 'ingerido')"
                ),
                {"id": documento_id},
            ).scalar_one()
            session.execute(
                text(
                    "INSERT INTO evento_documento (documento_id, nome, tipo, caminho_arquivo) "
                    "SELECT id, nome, :tipo, NULL FROM documento WHERE id = :id"
                ),
                {"id": documento_id, "tipo": "reprocessado" if ja_ingerido else "ingerido"},
            )
            session.commit()
        logger.info(
            "documento ingerido",
            extra={
                "documento_id": documento_id,
                "trechos": len(trechos),
                "modelo": embeddings.nome_modelo,
                "duracao_ms": round((time.perf_counter() - inicio) * 1000),
            },
        )
    except ErroDeExtracao as erro:
        _marcar_falha(fabrica, documento_id, str(erro))
    except ModeloIndisponivel as erro:
        logger.warning("modelo de embeddings indisponível", extra={"documento_id": documento_id})
        _marcar_falha(
            fabrica,
            documento_id,
            f"{erro}. Provavelmente o download do modelo falhou; reenvie o arquivo para tentar "
            "de novo (rode scripts/baixar_modelo.py para baixá-lo antes).",
        )
    except Exception as erro:
        logger.exception("falha inesperada na ingestão", extra={"documento_id": documento_id})
        _marcar_falha(fabrica, documento_id, f"Erro interno ({type(erro).__name__})")


def _marcar_falha(fabrica: sessionmaker[Session], documento_id: int, mensagem: str) -> None:
    """Registra a falha sem tocar nos trechos antigos: a versão anterior continua consultável."""
    try:
        with fabrica() as session:
            session.execute(
                text(
                    "UPDATE documento SET estado = 'falhou', mensagem_erro = :m, "
                    f"{_SEM_PROGRESSO}, atualizado_em = now() WHERE id = :id"
                ),
                {"m": mensagem[:500], "id": documento_id},
            )
            session.commit()
    except Exception:
        logger.exception("não consegui registrar a falha", extra={"documento_id": documento_id})
