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

from app.ingestao.config import IngestaoSettings
from app.ingestao.divisao import dividir
from app.ingestao.extracao import EXTENSOES_ACEITAS, ErroDeExtracao, extrair
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
    if Path(nome).suffix.lower() not in EXTENSOES_ACEITAS:
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
    if nome.lower().endswith(".pdf") and not conteudo.lstrip().startswith(b"%PDF-"):
        raise ArquivoInvalido("O arquivo não é um PDF válido")

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
                    "atualizado_em = now() WHERE id = :id"
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
                "mensagem_erro = NULL, atualizado_em = now() WHERE id = :id"
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

    destino = config.arquivos_dir / str(documento_id) / f"v{versao}{Path(nome).suffix.lower()}"
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


def marcar_para_reprocessar(session: Session, documento_id: int) -> bool:
    """Volta o documento a 'pendente' (os trechos atuais seguem consultáveis até a troca)."""
    achou = session.execute(
        text(
            "UPDATE documento SET estado = 'pendente', mensagem_erro = NULL, "
            "atualizado_em = now() WHERE id = :id RETURNING id"
        ),
        {"id": documento_id},
    ).first()
    session.commit()
    return achou is not None


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
        extracao = extrair(Path(caminho), max_paginas=config.max_paginas)
        trechos = dividir(extracao.paginas, config.tamanho_trecho, config.sobreposicao_trecho)
        vetores = embeddings.gerar_trechos([t.texto for t in trechos])
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
                    "INSERT INTO trecho (documento_id, posicao, pagina, secao, texto, vetor, "
                    "modelo_embedding, dimensao) VALUES (:d, :pos, :pag, :sec, :t, "
                    "CAST(:v AS vector), :m, :dim)"
                ),
                [
                    {
                        "d": documento_id,
                        "pos": t.posicao,
                        "pag": t.pagina,
                        "sec": t.secao,
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
                    "aviso = :aviso, atualizado_em = now() WHERE id = :id"
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
                    "atualizado_em = now() WHERE id = :id"
                ),
                {"m": mensagem[:500], "id": documento_id},
            )
            session.commit()
    except Exception:
        logger.exception("não consegui registrar a falha", extra={"documento_id": documento_id})
