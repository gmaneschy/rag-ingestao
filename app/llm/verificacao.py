"""Verificação barata de suporte: nomes da resposta que não aparecem nos trechos citados.

A citação [n] só prova que o trecho existe, não que ele sustenta a frase. Um modelo pequeno
costuma atribuir a uma pessoa o que o trecho diz de outra ("Bronshtein, seu filho", quando o
trecho fala do marido de Lydia). Esta checagem pega o caso mais claro: um nome próprio da
resposta que NÃO está nos trechos citados no parágrafo, mas ESTÁ em outro trecho recuperado,
sinal de que o fato foi tirado de um trecho e creditado a outro.

Um nome que não está em trecho nenhum é ignorado de propósito: quase sempre é tradução
("União" por "Union"), e não dá para separar isso de uma invenção sem um modelo de linguagem.
É só um alerta: o aviso pede para conferir a fonte, não diz que a resposta está errada.
"""

import re
import unicodedata
from collections.abc import Sequence

from app.llm.prompt import extrair_citacoes

MAX_NOMES = 5
_PREFIXO = 5  # letras comparadas: "Korney" e "Kornei", "Rússia" e "Russia" se encontram
_CITACAO = re.compile(r"\[\d{1,3}(?:\s*[,;]\s*\d{1,3})*\]")
_FRASE = re.compile(r"(?<=[.!?:])\s+")
_PALAVRA = re.compile(r"[^\W\d_][\w’'-]*")
_TITULOS_DA_ANALISE = re.compile(r"^\s*(?:evid[êe]ncias|avalia[çc][ãa]o)\s*:?\s*$", re.IGNORECASE)


def _sem_acento(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in base if not unicodedata.combining(c))


def _nomes(frase: str) -> list[str]:
    """Palavras com inicial maiúscula no meio da frase.

    A primeira palavra é sempre maiúscula ("Sim", "Os"), então só conta se vier seguida de outra
    palavra maiúscula, como em "Korney Chukovsky".
    """
    palavras = _PALAVRA.findall(_CITACAO.sub(" ", frase))
    nomes = []
    for i, palavra in enumerate(palavras):
        if len(palavra) < 4 or not palavra[0].isupper() or palavra.isupper():
            continue
        if palavra.endswith(("’s", "'s")):
            continue
        if i == 0 and not (len(palavras) > 1 and palavras[1][0].isupper()):
            continue
        nomes.append(palavra)
    return nomes


def nomes_sem_suporte(texto: str, trechos: Sequence[str]) -> list[str]:
    """Nomes de cada parágrafo que faltam em todos os trechos que ele cita.

    `trechos` é a lista dos textos na ordem em que foram numerados no prompt ([1] = trechos[0]).
    Parágrafos sem citação não são verificados: já recebem o aviso de "não citou nenhum trecho".
    """
    achados: list[str] = []
    for paragrafo in re.split(r"\n+", texto):
        if not paragrafo.strip() or _TITULOS_DA_ANALISE.match(paragrafo):
            continue
        citados = extrair_citacoes(paragrafo, len(trechos))
        if not citados:
            continue
        base = " ".join(_sem_acento(trechos[n - 1]) for n in citados)
        outros = " ".join(
            _sem_acento(t) for i, t in enumerate(trechos, start=1) if i not in citados
        )
        for frase in _FRASE.split(paragrafo):
            for nome in _nomes(frase):
                prefixo = re.escape(_sem_acento(nome)[:_PREFIXO])
                if (
                    not re.search(r"\b" + prefixo, base)
                    and re.search(r"\b" + prefixo, outros)
                    and nome not in achados
                ):
                    achados.append(nome)
    return achados[:MAX_NOMES]
