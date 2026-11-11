"""Filtros de apresentação das páginas."""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt
from markupsafe import Markup, escape

FUSO = ZoneInfo("America/Sao_Paulo")

ROTULO_DO_ESTADO = {"pendente": "Processando", "processado": "Pronto", "falhou": "Falhou"}
ROTULO_DO_TIPO = {
    "corpo": "texto",
    "notas": "notas",
    "bibliografia": "bibliografia",
    "indice": "índice",
    "copyright": "copyright",
    "fragmento": "fragmento",
}


def data_br(valor: datetime | None) -> str:
    if valor is None:
        return "—"
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=UTC)
    return valor.astimezone(FUSO).strftime("%d/%m/%Y %H:%M")


def numero(valor: int | None) -> str:
    return "—" if valor is None else f"{valor:,}".replace(",", ".")


def estado(valor: str) -> str:
    return ROTULO_DO_ESTADO.get(valor, valor)


def restante(segundos: int | None) -> str:
    """'cerca de 3 min' a partir de segundos; vazio quando não há estimativa."""
    if segundos is None:
        return ""
    if segundos < 45:
        return "menos de 1 min"
    minutos = round(segundos / 60)
    if minutos < 60:
        return f"cerca de {minutos} min"
    horas, resto = divmod(minutos, 60)
    return f"cerca de {horas} h {resto:02d} min"


def tipo(valor: str) -> str:
    return ROTULO_DO_TIPO.get(valor, valor)


def resumo(texto: str, limite: int = 260) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[:limite].rsplit(" ", 1)[0] + "…"


_CITACAO = re.compile(r"\[(\d{1,3}(?:\s*[,;]\s*\d{1,3})*)\]")
# Markdown da resposta do modelo, que é texto não confiável: HTML cru fica desligado (vira texto
# escapado) e o markdown-it já recusa links javascript:. Quebra de linha simples vira <br>.
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(
    ["table", "strikethrough"]
)
# Pedaços onde uma citação [n] não deve virar link: blocos de código, links e as próprias tags.
_NAO_TEXTO = re.compile(r"(<pre.*?</pre>|<code.*?</code>|<a .*?</a>|<[^>]+>)", re.DOTALL)


def _linkar_citacoes(html: str, prefixo: str) -> str:
    def link(achado: re.Match[str]) -> str:
        numeros = re.findall(r"\d+", achado.group(1))
        links = ", ".join(
            f'<a href="#{escape(prefixo)}-{n}" class="citacao" data-fonte="{n}">{n}</a>'
            for n in numeros
        )
        return f"[{links}]"

    partes = _NAO_TEXTO.split(html)
    for i in range(0, len(partes), 2):  # as posições pares são texto puro
        partes[i] = _CITACAO.sub(link, partes[i])
    return "".join(partes)


def texto_citado(texto: str | None, prefixo: str = "fonte") -> Markup:
    """Resposta do modelo em Markdown (negrito, listas, tabelas, código), com cada [n] virando
    link para a fonte n. O prefixo separa os alvos quando há várias respostas na mesma página."""
    html = _MARKDOWN.render((texto or "").strip())
    return Markup(_linkar_citacoes(html, prefixo))
