"""Conversas e turnos: guardar, executar em segundo plano, cancelar e editar.

Um turno é uma pergunta e a sua resposta. A pergunta roda numa thread do servidor, não presa à
requisição: trocar de tela não a interrompe. O texto parcial e a etapa ficam em memória (um
processo só); o que importa, o resultado, vai para o banco. Se o servidor reinicia no meio, o
turno que estava gerando vira 'erro' na subida (`encerrar_interrompidos`).
"""

import json
import logging
import threading
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.consulta import servico as consulta_servico
from app.consulta.busca import ReindexacaoNecessaria
from app.consulta.config import ConsultaSettings
from app.consulta.reranker import Reordenador
from app.llm.acompanhamento import Acompanhamento, Cancelado
from app.llm.config import LLMSettings
from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
from app.llm.tipos import ModeloIndisponivel

logger = logging.getLogger("app.conversa")

TAMANHO_DO_TITULO = 60
PERGUNTA_MAXIMA = 2000

# Turnos em andamento neste processo: id do turno -> (geração, acompanhamento).
_EM_ANDAMENTO: dict[int, tuple[int, Acompanhamento]] = {}
_TRAVA = threading.Lock()


@dataclass(frozen=True)
class Execucao:
    """Tudo que a pergunta precisa, já resolvido na requisição que a iniciou."""

    embeddings: GeradorDeEmbeddings
    gerador: GeradorDeResposta
    llm: LLMSettings
    consulta: ConsultaSettings
    reordenador: Reordenador | None


def titulo_da(pergunta: str) -> str:
    texto = " ".join(pergunta.split())
    if len(texto) <= TAMANHO_DO_TITULO:
        return texto
    return texto[:TAMANHO_DO_TITULO].rsplit(" ", 1)[0] + "…"


# ---------- conversas ----------


def criar_conversa(session: Session, pergunta: str, config: dict[str, object]) -> int:
    conversa_id = session.execute(
        text("INSERT INTO conversa (titulo, config) VALUES (:t, CAST(:c AS jsonb)) RETURNING id"),
        {"t": titulo_da(pergunta), "c": json.dumps(config)},
    ).scalar_one()
    session.commit()
    return conversa_id


def salvar_config(session: Session, conversa_id: int, config: dict[str, object]) -> None:
    session.execute(
        text("UPDATE conversa SET config = CAST(:c AS jsonb) WHERE id = :id"),
        {"c": json.dumps(config), "id": conversa_id},
    )
    session.commit()


def renomear(session: Session, conversa_id: int, titulo: str) -> bool:
    titulo = " ".join(titulo.split())[:120]
    if not titulo:
        return False
    achou = session.execute(
        text("UPDATE conversa SET titulo = :t WHERE id = :id RETURNING id"),
        {"t": titulo, "id": conversa_id},
    ).first()
    session.commit()
    return achou is not None


def apagar(session: Session, conversa_id: int) -> bool:
    """Apaga a conversa e, com ela, as consultas dos seus turnos (a pergunta e a resposta não
    ficam no histórico depois de apagadas aqui). Interrompe o que estiver gerando."""
    ids = list(
        session.execute(
            text("SELECT id FROM turno WHERE conversa_id = :c"), {"c": conversa_id}
        ).scalars()
    )
    for turno_id in ids:
        _interromper(turno_id)
    session.execute(
        text(
            "DELETE FROM consulta WHERE id IN "
            "(SELECT consulta_id FROM turno WHERE conversa_id = :c AND consulta_id IS NOT NULL)"
        ),
        {"c": conversa_id},
    )
    achou = session.execute(
        text("DELETE FROM conversa WHERE id = :c RETURNING id"), {"c": conversa_id}
    ).first()
    session.commit()
    return achou is not None


def listar(session: Session, limite: int = 60) -> list[dict[str, object]]:
    return [
        dict(linha)
        for linha in session.execute(
            text(
                "SELECT c.id, c.titulo, EXISTS (SELECT 1 FROM turno t WHERE t.conversa_id = c.id "
                "AND t.estado = 'gerando') AS gerando FROM conversa c "
                "ORDER BY c.atualizada_em DESC, c.id DESC LIMIT :n"
            ),
            {"n": limite},
        ).mappings()
    ]


def ver(session: Session, conversa_id: int) -> dict[str, object] | None:
    linha = (
        session.execute(
            text("SELECT id, titulo, config FROM conversa WHERE id = :id"), {"id": conversa_id}
        )
        .mappings()
        .first()
    )
    return dict(linha) if linha else None


# ---------- turnos ----------

_COLUNAS_DO_TURNO = (
    "t.id, t.conversa_id, t.pergunta, t.estado, t.erro, t.aviso, t.diagnostico, t.criado_em, "
    "t.consulta_id, c.resposta, c.fontes, c.modelo_geracao, c.tokens_entrada, c.tokens_saida, "
    "c.latencia_ms, c.ajudou, c.tipo"
)


def turnos(session: Session, conversa_id: int) -> list[dict[str, object]]:
    return [
        dict(linha)
        for linha in session.execute(
            text(
                f"SELECT {_COLUNAS_DO_TURNO} FROM turno t LEFT JOIN consulta c "
                "ON c.id = t.consulta_id WHERE t.conversa_id = :c ORDER BY t.id"
            ),
            {"c": conversa_id},
        ).mappings()
    ]


def turno(session: Session, conversa_id: int, turno_id: int) -> dict[str, object] | None:
    linha = (
        session.execute(
            text(
                f"SELECT {_COLUNAS_DO_TURNO} FROM turno t LEFT JOIN consulta c "
                "ON c.id = t.consulta_id WHERE t.id = :t AND t.conversa_id = :c"
            ),
            {"t": turno_id, "c": conversa_id},
        )
        .mappings()
        .first()
    )
    return dict(linha) if linha else None


def situacao(turno_id: int) -> tuple[str, str]:
    """(etapa, texto parcial) de um turno gerando agora; vazios se não houver."""
    with _TRAVA:
        item = _EM_ANDAMENTO.get(turno_id)
    return item[1].situacao() if item else ("", "")


def novo_turno(session: Session, conversa_id: int, pergunta: str) -> int:
    turno_id = session.execute(
        text("INSERT INTO turno (conversa_id, pergunta) VALUES (:c, :p) RETURNING id"),
        {"c": conversa_id, "p": pergunta[:PERGUNTA_MAXIMA]},
    ).scalar_one()
    session.execute(
        text("UPDATE conversa SET atualizada_em = now() WHERE id = :c"), {"c": conversa_id}
    )
    session.commit()
    return turno_id


def _interromper(turno_id: int) -> None:
    with _TRAVA:
        item = _EM_ANDAMENTO.get(turno_id)
    if item:
        item[1].cancelar()


def cancelar(session: Session, conversa_id: int, turno_id: int) -> bool:
    """Pede para parar. Se ninguém neste processo está gerando (ficou órfão), encerra direto."""
    _interromper(turno_id)
    with _TRAVA:
        rodando = turno_id in _EM_ANDAMENTO
    if not rodando:
        session.execute(
            text(
                "UPDATE turno SET estado = 'cancelada' "
                "WHERE id = :t AND conversa_id = :c AND estado = 'gerando'"
            ),
            {"t": turno_id, "c": conversa_id},
        )
        session.commit()
    return True


def reiniciar_turno(
    session: Session, conversa_id: int, turno_id: int, pergunta: str | None = None
) -> int | None:
    """Prepara o turno para gerar de novo (editar a pergunta, ou tentar outra vez). A consulta
    da resposta antiga é apagada: era a resposta a uma pergunta que deixou de valer. Devolve a
    nova geração, ou None se o turno não existe."""
    _interromper(turno_id)
    antigo = session.execute(
        text("SELECT consulta_id FROM turno WHERE id = :t AND conversa_id = :c FOR UPDATE"),
        {"t": turno_id, "c": conversa_id},
    ).first()
    if antigo is None:
        return None
    geracao = session.execute(
        text(
            "UPDATE turno SET estado = 'gerando', geracao = geracao + 1, consulta_id = NULL, "
            "erro = NULL, aviso = NULL, diagnostico = NULL, "
            "pergunta = COALESCE(:p, pergunta) WHERE id = :t RETURNING geracao"
        ),
        {"p": pergunta[:PERGUNTA_MAXIMA] if pergunta else None, "t": turno_id},
    ).scalar_one()
    if antigo[0] is not None:
        session.execute(text("DELETE FROM consulta WHERE id = :i"), {"i": antigo[0]})
    session.execute(
        text("UPDATE conversa SET atualizada_em = now() WHERE id = :c"), {"c": conversa_id}
    )
    session.commit()
    return geracao


def iniciar(turno_id: int, geracao: int) -> Acompanhamento:
    """Registra o acompanhamento ANTES de a thread começar, para o 'parar' já valer."""
    acompanhamento = Acompanhamento()
    with _TRAVA:
        _EM_ANDAMENTO[turno_id] = (geracao, acompanhamento)
    return acompanhamento


def _encerrar(
    fabrica: sessionmaker[Session], turno_id: int, geracao: int, estado: str, **campos: object
) -> None:
    """Grava o fim do turno só se ainda é a mesma geração e ninguém o encerrou antes."""
    colunas = ", ".join(f"{k} = :{k}" for k in campos)
    with fabrica() as session:
        session.execute(
            text(
                f"UPDATE turno SET estado = :estado{', ' + colunas if colunas else ''} "
                "WHERE id = :t AND geracao = :g AND estado = 'gerando'"
            ),
            {"estado": estado, "t": turno_id, "g": geracao, **campos},
        )
        session.commit()


def executar(
    fabrica: sessionmaker[Session],
    turno_id: int,
    geracao: int,
    acompanhamento: Acompanhamento,
    pergunta: str,
    config: dict[str, object],
    execucao: Execucao,
) -> None:
    """Corpo da thread: faz a pergunta e grava o resultado. Nunca levanta."""
    try:
        with fabrica() as session:
            resultado = consulta_servico.perguntar(
                session,
                pergunta,
                embeddings=execucao.embeddings,
                gerador=execucao.gerador,
                llm=execucao.llm,
                consulta=execucao.consulta,
                k=config.get("k"),
                colecoes=config.get("colecoes") or None,
                modo=config.get("modo", "hibrida"),
                tipo=config.get("tipo", "factual"),
                documento_ids=config.get("documento_ids") or None,
                incluir_tipos=config.get("incluir") or None,
                reordenador=execucao.reordenador,
                acompanhar=acompanhamento,
            )
        with fabrica() as session:
            ainda_vale = session.execute(
                text(
                    "UPDATE turno SET estado = 'pronta', consulta_id = :c, aviso = :a, "
                    "diagnostico = CAST(:d AS jsonb) "
                    "WHERE id = :t AND geracao = :g AND estado = 'gerando' RETURNING id"
                ),
                {
                    "c": resultado.id,
                    "a": resultado.aviso,
                    "d": json.dumps(resultado.diagnostico),
                    "t": turno_id,
                    "g": geracao,
                },
            ).first()
            if ainda_vale is None:  # cancelada ou refeita enquanto terminava: a consulta sobra
                session.execute(text("DELETE FROM consulta WHERE id = :i"), {"i": resultado.id})
            session.commit()
    except Cancelado:
        _encerrar(fabrica, turno_id, geracao, "cancelada")
    except ReindexacaoNecessaria as erro:
        _encerrar(
            fabrica,
            turno_id,
            geracao,
            "erro",
            erro="Há documentos indexados com outro modelo de embeddings "
            f"({', '.join(erro.documentos)}). Reprocesse-os antes de consultar.",
        )
    except consulta_servico.LimiteDiarioExcedido as erro:
        _encerrar(fabrica, turno_id, geracao, "erro", erro=str(erro))
    except ModeloIndisponivel as erro:
        _encerrar(fabrica, turno_id, geracao, "erro", erro=f"Modelo indisponível: {erro}")
    except Exception:
        logger.exception("falha ao responder o turno", extra={"turno_id": turno_id})
        _encerrar(fabrica, turno_id, geracao, "erro", erro="Erro inesperado ao responder.")
    finally:
        with _TRAVA:
            if _EM_ANDAMENTO.get(turno_id, (None,))[0] == geracao:
                del _EM_ANDAMENTO[turno_id]


def encerrar_interrompidos(fabrica: sessionmaker[Session]) -> int:
    """Na subida: turno 'gerando' sem thread viva (o servidor reiniciou) vira erro."""
    with fabrica() as session:
        n = session.execute(
            text(
                "UPDATE turno SET estado = 'erro', erro = 'Interrompida: o servidor foi "
                "reiniciado enquanto respondia. Use “Tentar de novo”.' "
                "WHERE estado = 'gerando' RETURNING id"
            )
        ).rowcount
        session.commit()
    return n
