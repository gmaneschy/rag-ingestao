"""Busca híbrida: similaridade vetorial (pgvector) + busca textual (tsvector), fundidas por RRF.

A busca vetorial acha o sentido ("prazo de entrega" ~ "quando chega"); a textual acha o termo
exato (siglas, códigos, nomes próprios), onde a vetorial falha. A fusão usa só a posição de cada
trecho em cada lista (Reciprocal Rank Fusion), então não precisa comparar notas de escalas
diferentes.

Os filtros (coleção, documento, tipo do trecho, seção, faixa de páginas) entram na própria
consulta SQL, antes de qualquer trecho sair do banco. Por padrão só o `corpo` é consultado:
notas, bibliografia, copyright e legendas ficam de fora, a não ser que o pedido as inclua.
Depois da fusão, trechos quase idênticos (a mesma orelha em três páginas) contam uma vez só.
"""

import re
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
    secao: str | None = None
    nota_reranker: float | None = None  # só se um reordenador passou por aqui


def vetor_como_texto(vetor: list[float]) -> str:
    return "[" + ",".join(f"{v:.8g}" for v in vetor) + "]"


TIPOS_PADRAO: tuple[str, ...] = ("corpo",)
LIMIAR_DUPLICADO = 0.8  # Jaccard das palavras a partir do qual dois trechos são "o mesmo"
_PALAVRA = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class Filtros:
    colecoes: list[str] | None = None
    secao: str | None = None  # contém (sem diferenciar maiúsculas) no nome da seção
    pagina_de: int | None = None
    pagina_ate: int | None = None
    documento_ids: list[int] | None = None
    tipos: tuple[str, ...] | None = TIPOS_PADRAO  # None = todos os tipos


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtro(filtros: Filtros | list[str] | None) -> tuple[str, dict[str, object]]:
    if not isinstance(filtros, Filtros):
        filtros = Filtros(colecoes=filtros, tipos=None)
    sql, parametros = "", {}
    if filtros.colecoes:
        sql += " AND d.colecao = ANY(:colecoes)"
        parametros["colecoes"] = filtros.colecoes
    if filtros.documento_ids:
        sql += " AND d.id = ANY(:documento_ids)"
        parametros["documento_ids"] = filtros.documento_ids
    if filtros.tipos:
        sql += " AND t.tipo = ANY(:tipos)"
        parametros["tipos"] = list(filtros.tipos)
    if filtros.secao:
        sql += " AND t.secao ILIKE :secao"
        parametros["secao"] = f"%{_escapar_like(filtros.secao)}%"
    if filtros.pagina_de is not None:
        sql += " AND t.pagina >= :pagina_de"
        parametros["pagina_de"] = filtros.pagina_de
    if filtros.pagina_ate is not None:
        sql += " AND t.pagina <= :pagina_ate"
        parametros["pagina_ate"] = filtros.pagina_ate
    return sql, parametros


_CAMPOS = "t.id, t.posicao, t.pagina, t.secao, t.texto, d.id AS documento_id, d.nome"


def _buscar_vetorial(session: Session, vetor: list[float], n: int, filtros: Filtros):
    """Os n trechos mais parecidos com a pergunta.

    Sem filtro de documento, coleção, seção ou página, usa o índice HNSW. Com filtro NÃO pode: o
    índice devolve os ~40 vizinhos mais próximos de TODO o corpus e só depois o Postgres aplica o
    filtro, então, ao escolher um documento cujo assunto ou idioma está longe da pergunta, todos
    os 40 são de outros documentos e o resultado sai vazio (a recusa imediata, sem chamar o
    modelo). Com filtro, a busca é exata sobre os trechos selecionados: CTE MATERIALIZED impede o
    planejador de usar o índice, e o custo é proporcional ao tamanho da seleção.
    """
    filtro, parametros = _filtro(filtros)
    parametros = {"v": vetor_como_texto(vetor), "n": n, **parametros}
    com_selecao = any(
        (
            filtros.colecoes,
            filtros.documento_ids,
            filtros.secao,
            filtros.pagina_de is not None,
            filtros.pagina_ate is not None,
        )
    )
    if not com_selecao:
        return session.execute(
            text(
                f"SELECT {_CAMPOS}, 1 - (t.vetor <=> CAST(:v AS vector)) AS similaridade "
                "FROM trecho t JOIN documento d ON d.id = t.documento_id "
                f"WHERE true{filtro} ORDER BY t.vetor <=> CAST(:v AS vector) LIMIT :n"
            ),
            parametros,
        ).all()
    return session.execute(
        text(
            f"WITH alvo AS MATERIALIZED (SELECT {_CAMPOS}, t.vetor "
            "FROM trecho t JOIN documento d ON d.id = t.documento_id "
            f"WHERE true{filtro}) "
            "SELECT id, posicao, pagina, secao, texto, documento_id, nome, "
            "1 - (vetor <=> CAST(:v AS vector)) AS similaridade "
            "FROM alvo ORDER BY vetor <=> CAST(:v AS vector) LIMIT :n"
        ),
        parametros,
    ).all()


# Palavra que está em mais que esta fração dos trechos não distingue nada ("was", "what", "on"
# em perguntas em inglês, que a configuração 'portuguese' não trata como palavras vazias).
FRACAO_MAX_DO_TERMO = 0.25
# Em corpus minúsculo toda palavra é "comum" (1 de 3 trechos já passa de 25%): o filtro só vale
# a partir daqui.
MIN_TRECHOS_PARA_FILTRAR = 20


def _termos_raros(session: Session, pergunta: str) -> list[str]:
    """Radicais da pergunta que aparecem em até FRACAO_MAX_DO_TERMO dos trechos do corpus."""
    return list(
        session.execute(
            text(
                "WITH termos AS (SELECT unnest(tsvector_to_array(to_tsvector('portuguese', :p))) "
                "AS lexema), total AS (SELECT count(*) AS n FROM trecho) "
                "SELECT lexema FROM termos, total WHERE "
                "(SELECT count(*) FROM trecho t WHERE to_tsvector('portuguese', t.texto) "
                "@@ quote_literal(lexema)::tsquery) <= :fracao * total.n OR total.n < :minimo"
            ),
            {"p": pergunta, "fracao": FRACAO_MAX_DO_TERMO, "minimo": MIN_TRECHOS_PARA_FILTRAR},
        ).scalars()
    )


def _buscar_textual(session: Session, pergunta: str, n: int, filtros: Filtros):
    """Qualquer palavra rara da pergunta serve (OU), e quem casa mais palavras sobe no ranking.

    O padrão do Postgres exige TODAS as palavras (E), o que zera o resultado em perguntas
    naturais. As palavras vazias do português ("o", "de", "qual") já saem na configuração
    'portuguese'; as comuns demais no corpus (inclusive as de outro idioma) saem em
    `_termos_raros`, senão uma pergunta em inglês casaria com quase todos os trechos.
    """
    termos = _termos_raros(session, pergunta)
    if not termos:
        return []
    filtro, parametros = _filtro(filtros)
    return session.execute(
        text(
            "WITH q AS (SELECT (SELECT string_agg(quote_literal(x), ' | ') "
            "FROM unnest(CAST(:termos AS text[])) AS x)::tsquery AS consulta) "
            f"SELECT {_CAMPOS}, ts_rank_cd(to_tsvector('portuguese', t.texto), q.consulta) AS nota "
            "FROM trecho t JOIN documento d ON d.id = t.documento_id, q "
            f"WHERE to_tsvector('portuguese', t.texto) @@ q.consulta{filtro} "
            "ORDER BY nota DESC, t.id LIMIT :n"
        ),
        {"termos": termos, "n": n, **parametros},
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
                    id=linha.id,
                    documento=linha.nome,
                    pagina=linha.pagina,
                    texto=linha.texto,
                    secao=linha.secao,
                ),
                documento_id=linha.documento_id,
                posicao=linha.posicao,
                similaridade=float(linha.similaridade) if rv is not None else None,
                rank_vetorial=rv,
                rank_textual=rt,
                pontuacao=pontuacao,
                secao=linha.secao,
            )
        )
    achados.sort(key=lambda a: (-a.pontuacao, -(a.similaridade or 0.0), a.trecho.id))
    return achados[:k]


def _palavras(texto: str) -> frozenset[str]:
    return frozenset(p.casefold() for p in _PALAVRA.findall(texto))


def deduplicar(achados: list[Achado], limiar: float = LIMIAR_DUPLICADO) -> list[Achado]:
    """Tira os trechos quase idênticos a um que já subiu no ranking (fica o de melhor posição).

    Compara o conjunto de palavras, então variações de OCR ("inmost" por "in most") ainda casam.
    """
    mantidos: list[Achado] = []
    conjuntos: list[frozenset[str]] = []
    for achado in achados:
        palavras = _palavras(achado.trecho.texto)
        repetido = any(
            palavras and outro and len(palavras & outro) / len(palavras | outro) >= limiar
            for outro in conjuntos
        )
        if not repetido:
            mantidos.append(achado)
            conjuntos.append(palavras)
    return mantidos


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
    secao: str | None = None,
    pagina_de: int | None = None,
    pagina_ate: int | None = None,
    documento_ids: list[int] | None = None,
    tipos: tuple[str, ...] | None = TIPOS_PADRAO,
    remover_duplicados: bool = True,
) -> list[Achado]:
    filtros = Filtros(colecoes, secao, pagina_de, pagina_ate, documento_ids, tipos)
    vetoriais = _buscar_vetorial(session, vetor, candidatos, filtros) if modo != "textual" else []
    textuais = _buscar_textual(session, pergunta, candidatos, filtros) if modo != "vetorial" else []
    if not remover_duplicados:
        return fundir(vetoriais, textuais, rrf_k=rrf_k, k=k)
    # Funde todos os candidatos, tira as repetições e só então corta em k.
    todos = fundir(vetoriais, textuais, rrf_k=rrf_k, k=len(vetoriais) + len(textuais))
    return deduplicar(todos)[:k]


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


def explicar_filtros(session: Session, colecoes: list[str] | None, secao: str | None) -> str | None:
    """Por que a busca não achou nada: coleção que não existe ou seção que não casa com nada."""
    if colecoes:
        existentes = set(
            session.execute(
                text("SELECT DISTINCT colecao FROM documento WHERE colecao = ANY(:c)"),
                {"c": colecoes},
            ).scalars()
        )
        faltam = [c for c in colecoes if c not in existentes]
        if faltam:
            return f"Coleção sem documentos: {', '.join(faltam)}."
    if secao:
        filtro, parametros = _filtro(Filtros(colecoes, secao, tipos=None))
        casam = session.execute(
            text(
                "SELECT count(*) FROM trecho t JOIN documento d ON d.id = t.documento_id "
                f"WHERE true{filtro}"
            ),
            parametros,
        ).scalar_one()
        if not casam:
            return f"Nenhum trecho em seção que contenha '{secao}'."
    return None
