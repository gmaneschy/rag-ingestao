"""Remoção de documentos sem deixar rastro.

Apagar um documento tem de apagar tudo o que veio dele:
  - os trechos e os vetores (ON DELETE CASCADE, na mesma transação);
  - o arquivo original, em disco (depois do commit; se falhar, fica uma limpeza pendente que é
    repetida na próxima subida da aplicação);
  - os trechos citados nas respostas antigas (`consulta.fontes`), que são redigidos ou, a pedido,
    as consultas inteiras são apagadas.

Fica apenas um registro em `evento_documento` (id, nome, tipo, data), sem conteúdo nenhum.
A pergunta e o texto da resposta de uma consulta redigida permanecem: a resposta pode ter
parafraseado o documento, e isso só se remove apagando a consulta (consultas="apagar").
"""

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.ingestao.config import IngestaoSettings

logger = logging.getLogger("app.ingestao")

ModoConsultas = Literal["redigir", "apagar"]


@dataclass(frozen=True)
class Remocao:
    documento_id: int
    nome: str
    colecao: str
    trechos_removidos: int
    consultas_redigidas: int
    consultas_apagadas: int
    arquivo_removido: bool


def _pasta_do_documento(config: IngestaoSettings, documento_id: int) -> Path:
    return config.arquivos_dir / str(documento_id)


def _dentro_de_arquivos(caminho: Path, config: IngestaoSettings) -> bool:
    """Só se apaga o que está dentro da pasta de arquivos, mesmo que o banco diga outra coisa."""
    raiz = config.arquivos_dir.resolve()
    alvo = caminho.resolve()
    return alvo != raiz and alvo.is_relative_to(raiz)


def _apagar_pasta(caminho: Path, config: IngestaoSettings) -> bool:
    """True se a pasta não existe mais ao final. Levanta ValueError para caminho fora da raiz."""
    if not _dentro_de_arquivos(caminho, config):
        raise ValueError(f"caminho fora da pasta de arquivos: {caminho}")
    try:
        if caminho.exists():
            shutil.rmtree(caminho)
    except OSError:
        logger.warning("não consegui apagar a pasta do documento", extra={"caminho": str(caminho)})
        return False
    return True


def _cita_o_documento(fonte: dict[str, object], documento_id: int, nome: str) -> bool:
    if "documento_id" in fonte:
        return fonte["documento_id"] == documento_id
    return fonte.get("documento") == nome  # consultas antigas, gravadas sem o id


def _redigir(fonte: dict[str, object]) -> dict[str, object]:
    """Sobra só a posição [n] e a marca de que o trecho foi removido."""
    return {"n": fonte.get("n"), "removido": True}


def _consultas_que_citam(session: Session, documento_id: int, nome: str):
    candidatas = session.execute(
        text(
            "SELECT id, fontes FROM consulta "
            "WHERE fontes @> CAST(:por_id AS jsonb) OR fontes @> CAST(:por_nome AS jsonb) "
            "ORDER BY id"
        ),
        {
            "por_id": json.dumps([{"documento_id": documento_id}]),
            "por_nome": json.dumps([{"documento": nome}], ensure_ascii=False),
        },
    ).all()
    return [
        (cid, fontes)
        for cid, fontes in candidatas
        if any(_cita_o_documento(f, documento_id, nome) for f in fontes)
    ]


def remover(
    session: Session,
    documento_id: int,
    *,
    consultas: ModoConsultas,
    config: IngestaoSettings,
    ator: str = "api",
) -> Remocao | None:
    """Remove o documento e tudo o que veio dele. None se o documento não existe."""
    linha = session.execute(
        text("SELECT id, nome, colecao FROM documento WHERE id = :id FOR UPDATE"),
        {"id": documento_id},
    ).first()
    if linha is None:
        session.rollback()
        return None
    _, nome, colecao = linha

    trechos = session.execute(
        text("SELECT count(*) FROM trecho WHERE documento_id = :id"), {"id": documento_id}
    ).scalar_one()

    redigidas = apagadas = 0
    afetadas = _consultas_que_citam(session, documento_id, nome)
    if consultas == "apagar":
        if afetadas:
            session.execute(
                text("DELETE FROM consulta WHERE id = ANY(:ids)"),
                {"ids": [cid for cid, _ in afetadas]},
            )
        apagadas = len(afetadas)
    else:
        for cid, fontes in afetadas:
            novas = [_redigir(f) if _cita_o_documento(f, documento_id, nome) else f for f in fontes]
            session.execute(
                text("UPDATE consulta SET fontes = CAST(:f AS jsonb) WHERE id = :id"),
                {"f": json.dumps(novas, ensure_ascii=False), "id": cid},
            )
        redigidas = len(afetadas)

    pasta = _pasta_do_documento(config, documento_id)
    session.execute(text("DELETE FROM documento WHERE id = :id"), {"id": documento_id})
    evento_id = session.execute(
        text(
            "INSERT INTO evento_documento (documento_id, nome, tipo, ator, limpeza_pendente, "
            "caminho_arquivo) VALUES (:d, :n, 'removido', :a, true, :p) RETURNING id"
        ),
        {"d": documento_id, "n": nome, "a": ator, "p": str(pasta)},
    ).scalar_one()
    session.commit()  # daqui em diante o documento já não existe para a busca

    removido = _apagar_pasta_e_registrar(session, evento_id, pasta, config)
    logger.info(
        "documento removido",
        extra={
            "documento_id": documento_id,
            "trechos": trechos,
            "consultas_redigidas": redigidas,
            "consultas_apagadas": apagadas,
            "arquivo_removido": removido,
        },
    )
    return Remocao(documento_id, nome, colecao, trechos, redigidas, apagadas, removido)


def _apagar_pasta_e_registrar(
    session: Session, evento_id: int, pasta: Path, config: IngestaoSettings
) -> bool:
    try:
        removido = _apagar_pasta(pasta, config)
    except ValueError:
        logger.error("caminho de limpeza fora da pasta de arquivos; ignorado")
        removido = True  # não é seguro apagar: não adianta tentar de novo
    if removido:
        session.execute(
            text(
                "UPDATE evento_documento SET limpeza_pendente = false, caminho_arquivo = NULL "
                "WHERE id = :id"
            ),
            {"id": evento_id},
        )
        session.commit()
    return removido


def limpar_pendentes(fabrica: sessionmaker[Session], config: IngestaoSettings) -> int:
    """Repete as limpezas de arquivo que falharam. Devolve quantas foram concluídas."""
    concluidas = 0
    with fabrica() as session:
        pendentes = session.execute(
            text(
                "SELECT id, caminho_arquivo FROM evento_documento "
                "WHERE limpeza_pendente ORDER BY id"
            )
        ).all()
        for evento_id, caminho in pendentes:
            if caminho and _apagar_pasta_e_registrar(session, evento_id, Path(caminho), config):
                concluidas += 1
            elif not caminho:
                session.execute(
                    text("UPDATE evento_documento SET limpeza_pendente = false WHERE id = :id"),
                    {"id": evento_id},
                )
                session.commit()
    return concluidas
