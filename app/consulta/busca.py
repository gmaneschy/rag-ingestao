"""Busca híbrida: similaridade vetorial (pgvector) + busca textual (tsvector), fundidas por RRF.

A busca vetorial acha o sentido ("prazo de entrega" ~ "quando chega"); a textual acha o termo
exato (siglas, códigos, nomes próprios), onde a vetorial falha. A fusão usa só a posição de cada
trecho em cada lista (Reciprocal Rank Fusion), então não precisa comparar notas de escalas
diferentes.

O filtro de coleção entra na própria consulta SQL, antes de qualquer trecho sair do banco.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.llm.tipos import TrechoRecuperado

Modo = Literal["hibrida", "vetorial", "textual"]


class ReindexacaoNecessaria(Exception):
    """Há trechos gerados por outro modelo de embeddings: misturar vetores daria lixo."""

    def __init__(self, documentos: list[str]) -> None:
        super().__init__(", ".join(documentos))
        self.documentos = documentos


@dataclass(frozen=True)
class Achado:
    trecho: TrechoRecuperado
    documento_id: int
    posicao: int
    similaridade: float | None  # cosseno, só se veio da busca vetorial
    rank_vetorial: int | None  # 1 = melhor
    rank_textual: int | None
    pontuacao: float


def vetor_como_texto(vetor: list[float]) -> str:
    return "[" + ",".join(f"{v:.8g}" for v in vetor) + "]"


def _filtro(colecoes: list[str] | None) -> tuple[str, dict[str, object]]:
    if not colecoes:
        return "", {}
    return " AND d.colecao = ANY(:colecoes)", {"colecoes": colecoes}


_CAMPOS = "t.id, t.posicao, t.pagina, t.texto, d.id AS documento_id, d.nome"


def _buscar_vetorial(session: Session, vetor: list[float], n: int, colecoes: list[str] | None):
    filtro, parametros = _filtro(colecoes)
    return session.execute(
        text(
            f"SELECT {_CAMPOS}, 1 - (t.vetor <=> CAST(:v AS vector)) AS similaridade "
            "FROM trecho t JOIN documento d ON d.id = t.documento_id "
            f"WHERE true{filtro} ORDER BY t.vetor <=> CAST(:v AS vector) LIMIT :n"
        ),
        {"v": vetor_como_texto(vetor), "n": n, **parametros},
    ).all()


def _buscar_textual(session: Session, pergunta: str, n: int, colecoes: list[str] | None):
    """Qualquer palavra da pergunta serve (OU), e quem casa mais palavras sobe no ranking.

    O padrão do Postgres exige TODAS as palavras (E), o que zera o resultado em perguntas
    naturais. As palavras vazias ("o", "de", "qual") já saem na configuração 'portuguese'.
    """
    filtro, parametros = _filtro(colecoes)
    return session.execute(
        text(
            "WITH q AS (SELECT to_tsquery('portuguese', "
            "replace(plainto_tsquery('portuguese', :p)::text, '&', '|')) AS consulta) "
            f"SELECT {_CAMPOS}, ts_rank_cd(to_tsvector('portuguese', t.texto), q.consulta) AS nota "
            "FROM trecho t JOIN documento d ON d.id = t.documento_id, q "
            f"WHERE to_tsvector('portuguese', t.texto) @@ q.consulta{filtro} "
            "ORDER BY nota DESC, t.id LIMIT :n"
        ),
        {"p": pergunta, "n": n, **parametros},
    ).all()


def fundir(vetoriais: list, textuais: list, *, rrf_k: int, k: int) -> list[Achado]:
    """Combina as duas listas (já ordenadas) por RRF: nota = soma de 1/(rrf_k + posição)."""
    por_id: dict[int, dict] = {}
    for rank, linha in enumerate(vetoriais, start=1):
        por_id.setdefault(linha.id, {"linha": linha})["rv"] = rank
    for rank, linha in enumerate(textuais, start=1):
        por_id.setdefault(linha.id, {"linha": linha})["rt"] = rank

    achados = []
    for dados in por_id.values():
        linha = dados["linha"]
        rv, rt = dados.get("rv"), dados.get("rt")
        pontuacao = sum(1 / (rrf_k + r) for r in (rv, rt) if r is not None)
        achados.append(
            Achado(
                trecho=TrechoRecuperado(
                    id=linha.id, documento=linha.nome, pagina=linha.pagina, texto=linha.texto
                ),
                documento_id=linha.documento_id,
                posicao=linha.posicao,
                similaridade=float(linha.similaridade) if rv is not None else None,
                rank_vetorial=rv,
                rank_textual=rt,
                pontuacao=pontuacao,
            )
        )
    achados.sort(key=lambda a: (-a.pontuacao, -(a.similaridade or 0.0), a.trecho.id))
    return achados[:k]


def buscar(
    session: Session,
    pergunta: str,
    vetor: list[float] | None,
    *,
    modo: Modo = "hibrida",
    k: int = 5,
    candidatos: int = 20,
    rrf_k: int = 60,
    colecoes: list[str] | None = None,
) -> list[Achado]:
    vetoriais = _buscar_vetorial(session, vetor, candidatos, colecoes) if modo != "textual" else []
    textuais = (
        _buscar_textual(session, pergunta, candidatos, colecoes) if modo != "vetorial" else []
    )
    return fundir(vetoriais, textuais, rrf_k=rrf_k, k=k)


def tem_evidencia(achados: list[Achado], limiar_similaridade: float) -> bool:
    """Há evidência se alguma palavra da pergunta aparece nos trechos ou se algum é parecido."""
    return any(
        a.rank_textual is not None
        or (a.similaridade is not None and a.similaridade >= limiar_similaridade)
        for a in achados
    )


def verificar_modelo(
    session: Session, nome_modelo: str, dimensao: int, colecoes: list[str] | None
) -> None:
    """Recusa consultar se algum trecho foi vetorizado por outro modelo."""
    filtro, parametros = _filtro(colecoes)
    nomes = session.execute(
        text(
            "SELECT DISTINCT d.nome FROM trecho t JOIN documento d ON d.id = t.documento_id "
            f"WHERE (t.modelo_embedding <> :m OR t.dimensao <> :dim){filtro} ORDER BY d.nome "
            "LIMIT 5"
        ),
        {"m": nome_modelo, "dim": dimensao, **parametros},
    ).scalars()
    nomes = list(nomes)
    if nomes:
        raise ReindexacaoNecessaria(nomes)
