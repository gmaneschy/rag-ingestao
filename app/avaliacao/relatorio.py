"""Do resultado bruto às métricas e às tabelas em Markdown para o README."""

from typing import Any

from app.avaliacao import metricas
from app.avaliacao.executor import RelatorioModo, ResultadoCaso

KS_PADRAO = (1, 3, 5, 10)
LIMIARES_PADRAO = tuple(round(x * 0.05, 2) for x in range(0, 13))  # 0.00 a 0.60


def resumir(rel: RelatorioModo, ks: tuple[int, ...] = KS_PADRAO) -> dict[str, Any]:
    com = [r for r in rel.casos if r.caso.responde]
    sem = [r for r in rel.casos if not r.caso.responde]
    posicoes = [r.posicao for r in com]
    resumo: dict[str, Any] = {
        "rotulo": rel.rotulo,
        "modo": rel.modo,
        "perguntas_com_resposta": len(com),
        "perguntas_sem_resposta": len(sem),
        "latencia_busca_media_ms": metricas.media([r.latencia_busca_ms for r in rel.casos]),
    }
    for k in ks:
        if k <= rel.k:
            resumo[f"recall@{k}"] = metricas.recall_em_k(posicoes, k)
    resumo[f"mrr@{rel.k}"] = metricas.mrr(posicoes, rel.k)
    # Decisão do portão de evidência (sem LLM): recusar = não ter evidência.
    resumo["recusa_correta_portao"] = metricas.razao(
        sum(not r.tem_evidencia for r in sem), len(sem)
    )
    resumo["recusa_indevida_portao"] = metricas.razao(
        sum(not r.tem_evidencia for r in com), len(com)
    )
    gerados = [r for r in rel.casos if r.resposta is not None]
    if gerados:
        resumo.update(_resumir_geracao(gerados))
        resumo["modelo_geracao"] = rel.modelo_geracao
    return resumo


def _resumir_geracao(gerados: list[ResultadoCaso]) -> dict[str, Any]:
    com = [r for r in gerados if r.caso.responde]
    sem = [r for r in gerados if not r.caso.responde]
    respondidas = [r for r in com if r.resposta and not r.resposta["recusou"]]
    latencias = [
        r.latencia_busca_ms + r.resposta["latencia_geracao_ms"] for r in gerados if r.resposta
    ]
    return {
        "recusa_correta": metricas.razao(sum(bool(r.resposta["recusou"]) for r in sem), len(sem)),
        "recusa_indevida": metricas.razao(sum(bool(r.resposta["recusou"]) for r in com), len(com)),
        # Fundamentação automática: das respondidas, quantas citam o trecho que sustenta a
        # resposta. É um piso: não lê a resposta, só confere de onde ela diz vir.
        "fundamentacao_auto": metricas.razao(
            sum(bool(r.resposta["citou_trecho_correto"]) for r in respondidas), len(respondidas)
        ),
        "com_aviso_de_nome": metricas.razao(
            sum(bool(r.resposta["nomes_sem_suporte"]) for r in respondidas), len(respondidas)
        ),
        "latencia_total_media_ms": metricas.media(latencias),
        "latencia_total_p95_ms": metricas.percentil(latencias, 0.95),
        "tokens_saida_medio": metricas.media(
            [r.resposta["tokens_saida"] for r in gerados if r.resposta]
        ),
    }


def varrer_limiar(
    rel: RelatorioModo, limiares: tuple[float, ...] = LIMIARES_PADRAO
) -> list[dict[str, Any]]:
    """Para cada limiar de similaridade, quanto o portão acerta sem chamar o LLM.

    Há evidência se alguma palavra da pergunta aparece nos trechos (leg textual) OU a melhor
    similaridade passa do limiar. Subir o limiar só ajuda onde o portão não é decidido pelo
    léxico: por isso a tabela mostra também quantas perguntas sem resposta têm acerto lexical.
    """
    com = [r for r in rel.casos if r.caso.responde]
    sem = [r for r in rel.casos if not r.caso.responde]

    def so_similaridade(r: ResultadoCaso, limiar: float) -> bool:
        sim = r.melhor_similaridade
        return sim is not None and sim >= limiar

    def evidencia(r: ResultadoCaso, limiar: float) -> bool:
        return r.ha_lexical or so_similaridade(r, limiar)

    linhas = []
    for limiar in limiares:
        linhas.append(
            {
                "limiar": limiar,
                "recusa_correta": metricas.razao(
                    sum(not evidencia(r, limiar) for r in sem), len(sem)
                ),
                "recusa_indevida": metricas.razao(
                    sum(not evidencia(r, limiar) for r in com), len(com)
                ),
                # O que aconteceria se só a similaridade decidisse (sem o atalho lexical).
                "recusa_correta_sim": metricas.razao(
                    sum(not so_similaridade(r, limiar) for r in sem), len(sem)
                ),
                "recusa_indevida_sim": metricas.razao(
                    sum(not so_similaridade(r, limiar) for r in com), len(com)
                ),
            }
        )
    return linhas


def tabela_portao(relatorios: list[RelatorioModo]) -> str:
    """O que o portão de evidência de fato decidiu em cada variação (sem o LLM)."""
    cab = ["Variação", "Critério do portão", "Recusa correta", "Recusa indevida"]
    linhas = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    for rel in relatorios:
        resumo = resumir(rel)
        if rel.nota_minima is not None:
            criterio = f"nota do reordenador >= {rel.nota_minima:g}"
        else:
            criterio = f"palavras ou similaridade >= {rel.limiar:g}"
        linhas.append(
            f"| {rel.rotulo} | {criterio} | {_pct(resumo['recusa_correta_portao'])} | "
            f"{_pct(resumo['recusa_indevida_portao'])} |"
        )
    return "\n".join(linhas)


def varrer_nota_reranker(rel: RelatorioModo) -> list[dict[str, Any]]:
    """Recusa correta/indevida se o portão fosse "a nota do primeiro trecho >= limiar".

    Os limiares são as próprias notas observadas: a escala do reordenador depende do modelo.
    Vazio se a rodada não usou reordenador.
    """
    pares = [(r.caso.responde, r.melhor_nota_reranker) for r in rel.casos]
    notas = sorted({n for _, n in pares if n is not None})
    com = [n for responde, n in pares if responde and n is not None]
    sem = [n for responde, n in pares if not responde and n is not None]
    linhas = []
    for limiar in notas:
        linhas.append(
            {
                "limiar": limiar,
                "recusa_correta": metricas.razao(sum(n < limiar for n in sem), len(sem)),
                "recusa_indevida": metricas.razao(sum(n < limiar for n in com), len(com)),
            }
        )
    return linhas


def melhor_limiar_reranker(linhas: list[dict[str, Any]]) -> dict[str, Any] | None:
    """O limiar que mais separa (acerto médio das duas classes); None se não há como medir."""
    validas = [
        ln
        for ln in linhas
        if ln["recusa_correta"] is not None and ln["recusa_indevida"] is not None
    ]
    if not validas:
        return None
    return max(validas, key=lambda ln: ln["recusa_correta"] + (1 - ln["recusa_indevida"]))


def tabela_nota_reranker(linhas: list[dict[str, Any]], maximo: int = 12) -> str:
    if not linhas:
        return ""
    passo = max(1, len(linhas) // maximo)
    escolhidas = linhas[::passo]
    melhor = melhor_limiar_reranker(linhas)
    if melhor is not None and melhor not in escolhidas:
        escolhidas = sorted([*escolhidas, melhor], key=lambda ln: ln["limiar"])
    cab = ["Nota mínima", "Recusa correta", "Recusa indevida", ""]
    saida = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    for ln in escolhidas:
        marca = "← melhor" if ln is melhor else ""
        saida.append(
            f"| {ln['limiar']:.3f} | {_pct(ln['recusa_correta'])} | "
            f"{_pct(ln['recusa_indevida'])} | {marca} |"
        )
    return "\n".join(saida)


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{valor * 100:.0f}%"


def _num(valor: float | None, casas: int = 0) -> str:
    return "—" if valor is None else f"{valor:.{casas}f}"


def tabela_recuperacao(resumos: list[dict[str, Any]]) -> str:
    ks = sorted({int(c.split("@")[1]) for r in resumos for c in r if c.startswith("recall@")})
    mrr_col = next((c for r in resumos for c in r if c.startswith("mrr@")), "mrr")
    cabecalho = ["Variação", "Modo", *[f"R@{k}" for k in ks], mrr_col.upper(), "Busca (ms)"]
    linhas = ["| " + " | ".join(cabecalho) + " |", "|" + "---|" * len(cabecalho)]
    for r in resumos:
        celulas = [r["rotulo"], r["modo"], *[_pct(r.get(f"recall@{k}")) for k in ks]]
        celulas += [_num(r.get(mrr_col), 2), _num(r["latencia_busca_media_ms"])]
        linhas.append("| " + " | ".join(celulas) + " |")
    return "\n".join(linhas)


def tabela_geracao(resumos: list[dict[str, Any]]) -> str:
    gerados = [r for r in resumos if "fundamentacao_auto" in r]
    if not gerados:
        return ""
    cab = [
        "Variação",
        "Modelo",
        "Recusa correta",
        "Recusa indevida",
        "Fundamentação*",
        "Latência (s)",
        "p95 (s)",
    ]
    linhas = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    for r in gerados:
        linhas.append(
            "| "
            + " | ".join(
                [
                    r["rotulo"],
                    str(r.get("modelo_geracao") or "—"),
                    _pct(r["recusa_correta"]),
                    _pct(r["recusa_indevida"]),
                    _pct(r["fundamentacao_auto"]),
                    _num((r["latencia_total_media_ms"] or 0) / 1000, 1),
                    _num((r["latencia_total_p95_ms"] or 0) / 1000, 1),
                ]
            )
            + " |"
        )
    linhas.append(
        "\n*Fundamentação automática: das respostas dadas, as que citam o trecho correto."
    )
    return "\n".join(linhas)


def tabela_limiar(linhas: list[dict[str, Any]]) -> str:
    cab = [
        "Limiar",
        "Recusa correta",
        "Recusa indevida",
        "Correta (só sim.)",
        "Indevida (só sim.)",
    ]
    saida = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    for linha in linhas:
        correta, indevida = _pct(linha["recusa_correta"]), _pct(linha["recusa_indevida"])
        c_sim, i_sim = _pct(linha["recusa_correta_sim"]), _pct(linha["recusa_indevida_sim"])
        saida.append(f"| {linha['limiar']:.2f} | {correta} | {indevida} | {c_sim} | {i_sim} |")
    return "\n".join(saida)


def casos_com_falha(rel: RelatorioModo) -> list[ResultadoCaso]:
    """Perguntas respondíveis cujo trecho correto não veio: as que valem ser lidas à mão."""
    return [r for r in rel.casos if r.caso.responde and r.posicao is None]
