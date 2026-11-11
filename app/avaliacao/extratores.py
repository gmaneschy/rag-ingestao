"""Compara extratores de texto de PDF (pypdf, pdfplumber, PyMuPDF) nos documentos do corpus.

Nada vai para o banco: lê os PDFs de uma pasta, extrai uma amostra de páginas com cada extrator e
mede velocidade e sinais de texto ruim. O texto passa pela mesma limpeza do pipeline
(`limpar` e `juntar_linhas`), para a comparação ser justa. O que a máquina não mede, a ordem de
leitura em colunas e tabelas, aparece em trechos lado a lado para você conferir.

O PyMuPDF usa a licença AGPL; ele fica só no grupo de desenvolvimento, para esta comparação.
"""

import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from app.ingestao.extracao import juntar_linhas, limpar

Leitor = Callable[[Path, list[int]], dict[int, str]]

_COLADA = re.compile(r"\S{26,}")
_ESPACADA = re.compile(r"(?:\b\w ){6,}\w\b")
_HIFEN = re.compile(r"\w- \w")
_LIXO = re.compile(r"�|\(cid:\d+\)")
_PALAVRA = re.compile(r"\w+", re.UNICODE)


def ler_pypdf(caminho: Path, numeros: list[int]) -> dict[int, str]:
    from pypdf import PdfReader

    leitor = PdfReader(caminho)
    return {n: leitor.pages[n - 1].extract_text() or "" for n in numeros}


def ler_pdfplumber(caminho: Path, numeros: list[int]) -> dict[int, str]:
    import pdfplumber

    with pdfplumber.open(caminho) as pdf:
        return {n: pdf.pages[n - 1].extract_text() or "" for n in numeros}


def ler_pymupdf(caminho: Path, numeros: list[int]) -> dict[int, str]:
    import pymupdf

    with pymupdf.open(caminho) as doc:
        return {n: doc[n - 1].get_text("text") or "" for n in numeros}


EXTRATORES: dict[str, Leitor] = {
    "pypdf": ler_pypdf,
    "pdfplumber": ler_pdfplumber,
    "pymupdf": ler_pymupdf,
}


@dataclass
class Resultado:
    segundos: float = 0.0
    paginas: int = 0
    vazias: int = 0
    palavras: int = 0
    coladas: int = 0
    espacadas: int = 0
    hifens: int = 0
    lixo: int = 0
    erro: str = ""
    textos: dict[int, str] = field(default_factory=dict)


def paginas_da_amostra(total: int, amostra: int) -> list[int]:
    """Até `amostra` páginas espalhadas por igual pelo documento (1 a total)."""
    if total <= amostra:
        return list(range(1, total + 1))
    passo = total / amostra
    return sorted({int(i * passo) + 1 for i in range(amostra)})


def normalizar(texto: str) -> str:
    return juntar_linhas(limpar(texto))


def medir(nome: str, leitor: Leitor, caminho: Path, numeros: list[int]) -> Resultado:
    r = Resultado()
    inicio = time.perf_counter()
    try:
        brutos = leitor(caminho, numeros)
    except Exception as erro:  # noqa: BLE001 - cada extrator falha de um jeito
        r.erro = f"{type(erro).__name__}: {str(erro)[:80]}"
        return r
    r.segundos = time.perf_counter() - inicio
    r.paginas = len(numeros)
    for n, bruto in brutos.items():
        texto = normalizar(bruto)
        r.textos[n] = texto
        if not texto:
            r.vazias += 1
        r.palavras += len(_PALAVRA.findall(texto))
        r.coladas += len(_COLADA.findall(texto))
        r.espacadas += len(_ESPACADA.findall(texto))
        r.hifens += len(_HIFEN.findall(texto))
        r.lixo += len(_LIXO.findall(texto))
    return r


def palavras_em_comum(a: str, b: str) -> float:
    """Fração de palavras que os dois textos compartilham (sem olhar a ordem)."""
    ca, cb = Counter(_PALAVRA.findall(a.lower())), Counter(_PALAVRA.findall(b.lower()))
    total = max(sum(ca.values()), sum(cb.values()))
    return sum((ca & cb).values()) / total if total else 1.0


def mesma_ordem(a: str, b: str, limite: int = 300) -> float:
    """Semelhança da sequência de palavras (1 = mesma ordem de leitura)."""
    pa, pb = _PALAVRA.findall(a.lower())[:limite], _PALAVRA.findall(b.lower())[:limite]
    if not pa and not pb:
        return 1.0
    return SequenceMatcher(None, pa, pb, autojunk=False).ratio()


@dataclass
class Documento:
    nome: str
    total_paginas: int
    amostra: list[int]
    resultados: dict[str, Resultado]
    concordancia: dict[str, tuple[float, float]]  # "a x b" -> (palavras em comum, mesma ordem)


def comparar_documento(
    caminho: Path, nomes: list[str], amostra: int, rotulo: str | None = None
) -> Documento:
    from pypdf import PdfReader

    total = len(PdfReader(caminho).pages)
    numeros = paginas_da_amostra(total, amostra)
    resultados = {n: medir(n, EXTRATORES[n], caminho, numeros) for n in nomes}
    concordancia: dict[str, tuple[float, float]] = {}
    ok = [n for n in nomes if not resultados[n].erro]
    for i, a in enumerate(ok):
        for b in ok[i + 1 :]:
            comuns, ordens = [], []
            for n in numeros:
                ta, tb = resultados[a].textos.get(n, ""), resultados[b].textos.get(n, "")
                if ta or tb:
                    comuns.append(palavras_em_comum(ta, tb))
                    ordens.append(mesma_ordem(ta, tb))
            if comuns:
                concordancia[f"{a} × {b}"] = (sum(comuns) / len(comuns), sum(ordens) / len(ordens))
    return Documento(rotulo or caminho.name, total, numeros, resultados, concordancia)


def _por_mil(valor: int, palavras: int) -> str:
    return f"{1000 * valor / palavras:.1f}" if palavras else "—"


def relatorio(documentos: list[Documento], nomes: list[str], amostra: int) -> str:
    linhas = [
        "# Comparação de extratores de PDF",
        "",
        f"{len(documentos)} documento(s); até {amostra} páginas por documento, espalhadas pelo "
        "arquivo. Contagens por mil palavras: menor é melhor.",
        "",
        "## Resumo por extrator",
        "",
        "| Extrator | ms/página | s por 100 páginas | páginas vazias | palavras coladas | "
        "letras espaçadas | hífen solto | lixo (cid, U+FFFD) | erros |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for nome in nomes:
        rs = [d.resultados[nome] for d in documentos]
        pag = sum(r.paginas for r in rs)
        seg = sum(r.segundos for r in rs)
        pal = sum(r.palavras for r in rs)
        vazias = sum(r.vazias for r in rs)
        linhas.append(
            f"| {nome} | {1000 * seg / pag:.1f} | {100 * seg / pag:.1f} | "
            f"{vazias} de {pag} | {_por_mil(sum(r.coladas for r in rs), pal)} | "
            f"{_por_mil(sum(r.espacadas for r in rs), pal)} | "
            f"{_por_mil(sum(r.hifens for r in rs), pal)} | "
            f"{_por_mil(sum(r.lixo for r in rs), pal)} | {sum(bool(r.erro) for r in rs)} |"
            if pag
            else f"| {nome} | — | — | — | — | — | — | — | {len(rs)} |"
        )
    pares = sorted({p for d in documentos for p in d.concordancia})
    if pares:
        linhas += [
            "",
            "## Concordância entre extratores (média dos documentos)",
            "",
            "Palavras em comum mede se leem o mesmo conteúdo; mesma ordem mede se leem na mesma "
            "ordem. Ordem baixa com palavras altas sugere colunas ou tabelas lidas de outro jeito.",
            "",
            "| Par | Palavras em comum | Mesma ordem |",
            "|---|---|---|",
        ]
        for par in pares:
            vs = [d.concordancia[par] for d in documentos if par in d.concordancia]
            linhas.append(
                f"| {par} | {100 * sum(v[0] for v in vs) / len(vs):.0f}% | "
                f"{100 * sum(v[1] for v in vs) / len(vs):.0f}% |"
            )
    linhas += ["", "## Por documento", ""]
    cab = ["Documento", "Págs.", *[f"{n} (ms/pág)" for n in nomes], "Ordem mínima entre pares"]
    linhas += ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    for d in documentos:
        ms = [
            "erro"
            if d.resultados[n].erro
            else f"{1000 * d.resultados[n].segundos / max(d.resultados[n].paginas, 1):.0f}"
            for n in nomes
        ]
        pior = min((v[1] for v in d.concordancia.values()), default=None)
        linhas.append(
            f"| {d.nome[-60:]} | {d.total_paginas} | {' | '.join(ms)} | "
            f"{'—' if pior is None else f'{100 * pior:.0f}%'} |"
        )
    erros = [(d.nome, n, r.erro) for d in documentos for n, r in d.resultados.items() if r.erro]
    if erros:
        linhas += ["", "## Erros", ""] + [f"- `{doc}` com {n}: {e}" for doc, n, e in erros]
    # Os 3 documentos em que os extratores mais discordam na ordem: é onde olhar à mão.
    discordantes = sorted(
        (d for d in documentos if d.concordancia),
        key=lambda d: min(v[1] for v in d.concordancia.values()),
    )[:3]
    if discordantes:
        linhas += ["", "## Onde conferir à mão (maior discordância de ordem)", ""]
        for d in discordantes:
            n = d.amostra[len(d.amostra) // 2]
            linhas += [f"### {d.nome}, página {n}", ""]
            for nome in nomes:
                texto = " ".join(d.resultados[nome].textos.get(n, "").split())[:350]
                linhas += [f"**{nome}:** {texto or '(vazio)'}", ""]
    return "\n".join(linhas) + "\n"


def executar(pasta: Path, nomes: list[str], amostra: int, limite_docs: int | None) -> str:
    # Importa antes de medir: o primeiro import de cada biblioteca não pode contar como lentidão.
    for biblioteca in ("pypdf", "pdfplumber", "pymupdf"):
        __import__(biblioteca)
    arquivos = sorted(pasta.rglob("*.pdf"))
    if limite_docs:
        arquivos = arquivos[:limite_docs]
    if not arquivos:
        raise SystemExit(f"Nenhum PDF em {pasta}")
    documentos = []
    for n, caminho in enumerate(arquivos, start=1):
        print(f"[{n}/{len(arquivos)}] {caminho.relative_to(pasta).as_posix()}", flush=True)
        try:
            documentos.append(
                comparar_documento(caminho, nomes, amostra, caminho.relative_to(pasta).as_posix())
            )
        except Exception as erro:  # noqa: BLE001 - um PDF ruim não pode parar a comparação
            print(f"  ignorado ({type(erro).__name__})", flush=True)
    return relatorio(documentos, nomes, amostra)
