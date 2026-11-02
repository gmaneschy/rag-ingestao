"""Filtros de apresentação das páginas."""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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


def tipo(valor: str) -> str:
    return ROTULO_DO_TIPO.get(valor, valor)


def resumo(texto: str, limite: int = 260) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[:limite].rsplit(" ", 1)[0] + "…"


_CITACAO = re.compile(r"\[(\d{1,3}(?:\s*[,;]\s*\d{1,3})*)\]")


def texto_citado(texto: str | None, prefixo: str = "fonte") -> Markup:
    """Texto da resposta em parágrafos, com cada [n] virando link para a fonte n.

    O prefixo separa os alvos quando há várias respostas na mesma página (comparação)."""

    def link(achado: re.Match[str]) -> str:
        numeros = re.findall(r"\d+", achado.group(1))
        links = ", ".join(
            f'<a href="#{escape(prefixo)}-{n}" class="citacao">{n}</a>' for n in numeros
        )
        return f"[{links}]"

    paragrafos = [p for p in re.split(r"\n\s*\n", (texto or "").strip()) if p.strip()]
    saida = []
    for p in paragrafos:
        seguro = str(escape(p.strip())).replace("\n", "<br>")
        saida.append(f"<p>{_CITACAO.sub(link, seguro)}</p>")
    return Markup("".join(saida))
