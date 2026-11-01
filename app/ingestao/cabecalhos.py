"""Cabeçalhos, rodapés e números de página correntes: ruído que se repete no topo ou no pé.

Uma linha como "98 The Case of Alexander Blok" ou "Lev Tolstoy as Early Modernist 43" aparece em
dezenas de páginas, com o número de página mudando. Se ficar no texto, polui os trechos (o modelo
lê o cabeçalho como se fosse conteúdo) e, pior, o detector de seções a toma por título. Aqui as
linhas são comparadas sem os dígitos: o que se repete em várias páginas sai do texto.

Um cabeçalho que traz o número da página e se repete só em parte do livro quase sempre é o título
do capítulo corrente; a primeira aparição vira o início de uma seção (ver `Corrido`).
"""

import re
from dataclasses import dataclass

from app.ingestao import secoes
from app.ingestao.secoes import Titulo

BORDA = 2  # quantas linhas do topo e do pé de cada página são examinadas
MIN_PAGINAS = 4  # a linha precisa aparecer em pelo menos tantas páginas
# Página com menos linhas que isso não é analisada: sobraria só o "cabeçalho" (folha de rosto,
# página quase vazia), e apagá-lo apagaria a página.
MIN_LINHAS_NA_PAGINA = 5
TAMANHO_MAX = 100
# Acima dessa fração das páginas a linha é título do livro ou do autor, não do capítulo.
FRACAO_MAX_DE_CAPITULO = 0.35
# Número de página solto (sem mais nada na linha) só sai se aparece em boa parte das páginas.
FRACAO_MIN_NUMEROS_SOLTOS = 0.30

# O número de página fica numa ponta da linha ("98 Título" ou "Título 43"); dígitos no meio de uma
# linha ("Linha 3 de 10", "Figura 2.1 mostra") são conteúdo e fazem linhas diferentes.
_NUMERO_NAS_PONTAS = re.compile(r"^\d+[\s.\-–—]*|[\s.\-–—]*\d+$")
_NAO_ALFANUMERICO = re.compile(r"[\W_]+")
_SO_NUMERO = re.compile(r"^\d{1,4}$")


@dataclass(frozen=True)
class Corrido:
    textos: list[tuple[int, str]]  # (página, texto) já sem os cabeçalhos
    titulos: dict[int, Titulo]  # página -> início de capítulo deduzido do cabeçalho
    removidas: int  # quantas linhas saíram, para o log


def _chave(linha: str) -> str:
    return _NAO_ALFANUMERICO.sub("", _NUMERO_NAS_PONTAS.sub("", linha.strip()).casefold())


def _sem_numero(linha: str) -> str:
    return re.sub(r"\s+", " ", _NUMERO_NAS_PONTAS.sub("", linha.strip())).strip(" .,:;-–—")


def _pontas(linhas: list[str]) -> list[int]:
    """Índices das linhas não vazias do topo e do pé; vazio se a página tem pouco texto."""
    indices = [i for i, linha in enumerate(linhas) if linha.strip()]
    if len(indices) < MIN_LINHAS_NA_PAGINA:
        return []
    return sorted({*indices[:BORDA], *indices[-BORDA:]})


def _candidata(linha: str) -> bool:
    """Linha que pode ser cabeçalho: curta, com letras, e que não é um título de verdade."""
    linha = linha.strip()
    if not linha or len(linha) > TAMANHO_MAX or len(_chave(linha)) < 3:
        return False
    titulo = secoes.titulo_da_linha(linha)
    # "Capítulo 5", "Introduction", "# Título" se repetem como títulos, não como cabeçalho.
    return not (titulo and titulo.origem in {"palavra", "markdown"})


def remover_corridos(textos: list[tuple[int, str]]) -> Corrido:
    """Tira cabeçalhos, rodapés e números de página repetidos; devolve também os capítulos."""
    if len(textos) < MIN_PAGINAS:
        return Corrido(textos, {}, 0)

    linhas_de = [texto.split("\n") for _, texto in textos]
    paginas_da_chave: dict[str, set[int]] = {}
    com_numero: dict[str, int] = {}
    ocorrencias: dict[str, int] = {}
    paginas_com_numero_solto = 0
    for pos, linhas in enumerate(linhas_de):
        tem_solto = False
        for i in _pontas(linhas):
            linha = linhas[i].strip()
            if _SO_NUMERO.match(linha):
                tem_solto = True
            elif _candidata(linha):
                chave = _chave(linha)
                paginas_da_chave.setdefault(chave, set()).add(pos)
                ocorrencias[chave] = ocorrencias.get(chave, 0) + 1
                if _NUMERO_NAS_PONTAS.search(linha.strip()):
                    com_numero[chave] = com_numero.get(chave, 0) + 1
        paginas_com_numero_solto += tem_solto

    total = len(textos)
    corridas = {c for c, paginas in paginas_da_chave.items() if len(paginas) >= MIN_PAGINAS}
    tirar_numeros = paginas_com_numero_solto >= max(MIN_PAGINAS, FRACAO_MIN_NUMEROS_SOLTOS * total)

    novos: list[tuple[int, str]] = []
    titulos: dict[int, Titulo] = {}
    ja_promovida: set[str] = set()
    removidas = 0
    for (numero, _), linhas in zip(textos, linhas_de, strict=True):
        tirar: set[int] = set()
        for i in _pontas(linhas):
            linha = linhas[i].strip()
            if tirar_numeros and _SO_NUMERO.match(linha):
                tirar.add(i)
            elif _candidata(linha) and _chave(linha) in corridas:
                tirar.add(i)
                chave = _chave(linha)
                capitulo = (
                    len(paginas_da_chave[chave]) <= FRACAO_MAX_DE_CAPITULO * total
                    and com_numero.get(chave, 0) >= 0.8 * ocorrencias[chave]
                )
                if capitulo and chave not in ja_promovida:
                    ja_promovida.add(chave)
                    titulos[numero] = Titulo(_sem_numero(linha), 1, "cabecalho", linha)
        # Cabeçalho que a extração deixou fora das pontas (a página abre com uma linha de texto
        # corrido, por exemplo): linha com número na ponta e o mesmo texto de um cabeçalho.
        for i, linha in enumerate(linhas):
            if (
                i not in tirar
                and _NUMERO_NAS_PONTAS.search(linha.strip())
                and _candidata(linha)
                and _chave(linha) in corridas
            ):
                tirar.add(i)
        removidas += len(tirar)
        restante = "\n".join(linha for i, linha in enumerate(linhas) if i not in tirar).strip()
        novos.append((numero, restante))
    return Corrido([(n, t) for n, t in novos if t], titulos, removidas)
