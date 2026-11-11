"""Perfis de trecho: combinações de tamanho, sobreposição e nota mínima para um tipo de texto.

O tamanho do trecho é medido em caracteres (em português, ~4 caracteres por token). A nota mínima
do reordenador depende do tamanho do trecho: só o perfil "curto" foi calibrado (com o conjunto de
referência do projeto e o jina-reranker-v2). Nos demais, a nota continua a configurada, até que
seja medida com `python -m app.avaliacao rodar --reranker ... --nota-minima ...` (ver docs).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Perfil:
    chave: str
    rotulo: str
    para_que: str
    tamanho: int | None
    sobreposicao: int | None
    nota_minima: float | None  # None: vale a nota configurada
    calibrado: bool


PERFIS: tuple[Perfil, ...] = (
    Perfil(
        "curto",
        "curto (~125 tokens)",
        "fatos e frases soltas: FAQs, notas, diários, trechos de biografia.",
        500,
        80,
        0.05,
        True,
    ),
    Perfil(
        "medio",
        "médio (~250 tokens)",
        "textos em artigos, cláusulas e seções: leis, contratos, manuais.",
        1000,
        150,
        None,
        False,
    ),
    Perfil(
        "longo",
        "longo (~450 tokens)",
        "prosa corrida: capítulos, relatórios, artigos acadêmicos.",
        1800,
        250,
        None,
        False,
    ),
)
POR_CHAVE = {p.chave: p for p in PERFIS}
PERSONALIZADO = "personalizado"
