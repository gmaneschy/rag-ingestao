"""Confere o gabarito contra o corpus: cada 'suporte' alcança pelo menos um trecho?

Um suporte que não casa com trecho nenhum (nome do documento errado, página fora da faixa, termo
que o texto extraído escreve de outro jeito) faz a pergunta contar como erro da busca quando o
erro é do gabarito. Rodar isto antes de medir evita tirar conclusões de um gabarito quebrado.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.avaliacao import metricas
from app.avaliacao.conjunto import Caso, Suporte
from app.llm.tipos import TrechoRecuperado


@dataclass(frozen=True)
class Linha:
    trecho: TrechoRecuperado
    tipo: str


def _termos(suporte: Suporte) -> list[str]:
    return [*suporte.contem, *suporte.contem_algum]


def diagnosticar(suporte: Suporte, linhas: Sequence[Linha]) -> tuple[bool, str]:
    """(ok, mensagem): ok se algum trecho de tipo 'corpo' casa com o suporte."""
    casam = [ln for ln in linhas if metricas.casa(suporte, ln.trecho)]
    if casam:
        tipos = Counter(ln.tipo for ln in casam)
        paginas = sorted({ln.trecho.pagina for ln in casam if ln.trecho.pagina is not None})
        resumo = f"{len(casam)} trecho(s), páginas {paginas[:8]}"
        if "corpo" not in tipos:
            return False, f"só em trechos fora do corpo ({dict(tipos)}): a busca padrão não os vê"
        return True, resumo

    do_documento = [ln for ln in linhas if metricas.documento_casa(suporte, ln.trecho)]
    if not do_documento:
        nomes = sorted({ln.trecho.documento for ln in linhas})
        return False, f"nenhum documento casa com {suporte.documento!r}. Existentes: {nomes}"
    if suporte.paginas:
        na_faixa = [
            ln
            for ln in do_documento
            if ln.trecho.pagina is not None
            and suporte.paginas[0] <= ln.trecho.pagina <= suporte.paginas[1]
        ]
        if not na_faixa:
            maximo = max((ln.trecho.pagina or 0) for ln in do_documento)
            achado = f"documento achado, mas sem trechos nas páginas {suporte.paginas}"
            return False, f"{achado} (vai até {maximo})"
    # Documento e páginas existem: o problema são os termos. Mostra onde cada um aparece.
    onde = []
    for termo in _termos(suporte):
        n = metricas.normalizar(termo)
        paginas = sorted(
            {
                ln.trecho.pagina
                for ln in do_documento
                if n in metricas.normalizar(ln.trecho.texto) and ln.trecho.pagina is not None
            }
        )
        onde.append(f"{termo!r}: {paginas[:8] if paginas else 'em nenhum trecho'}")
    return False, "nenhum trecho tem os termos juntos. Onde cada um aparece: " + "; ".join(onde)


def conferir(casos: Sequence[Caso], linhas: Sequence[Linha]) -> list[tuple[str, int, bool, str]]:
    """Uma linha por (caso, suporte): (id, número do suporte, ok, mensagem)."""
    saida = []
    for caso in casos:
        for i, suporte in enumerate(caso.suporte, start=1):
            ok, mensagem = diagnosticar(suporte, linhas)
            saida.append((caso.id, i, ok, mensagem))
    return saida
