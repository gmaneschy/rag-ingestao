"""Linha de comando da avaliação.

uv run python -m app.avaliacao inspecionar "Qual o nome da irmã de Lígia?"
uv run python -m app.avaliacao conferir   # o gabarito alcança trechos do corpus?
uv run python -m app.avaliacao rodar --modos hibrida,vetorial,textual
uv run python -m app.avaliacao falhas avaliacao/resultados/ARQUIVO.json
uv run python -m app.avaliacao embeddings --modelos MODELO1,MODELO2
uv run python -m app.avaliacao rodar --gerar --rotulo qwen2.5-7b
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import text

from app.avaliacao import relatorio
from app.avaliacao.conjunto import carregar
from app.avaliacao.executor import RelatorioModo, avaliar
from app.consulta import busca
from app.consulta.config import get_consulta_settings
from app.consulta.reranker import criar_reordenador
from app.db import get_sessionmaker
from app.ingestao.config import get_ingestao_settings
from app.llm.fabrica import criar_embeddings, criar_gerador_de_resposta

MODOS = ("hibrida", "vetorial", "textual")


def _inspecionar(args: argparse.Namespace) -> int:
    """Mostra o que a busca devolve, para escolher documento, páginas e termos do 'suporte'."""
    embeddings = criar_embeddings()
    with get_sessionmaker()() as session:
        achados = busca.buscar(
            session,
            args.pergunta,
            embeddings.gerar_consulta(args.pergunta),
            k=args.k,
            colecoes=args.colecoes,
        )
    for n, a in enumerate(achados, start=1):
        print(f"[{n}] {a.trecho.documento} p.{a.trecho.pagina} sim={a.similaridade}")
        print("    " + " ".join(a.trecho.texto.split())[:300])
    return 0


def _rastros(args: argparse.Namespace) -> int:
    """Procura um trecho de texto em todo o banco e na pasta de arquivos: prova da remoção."""
    from app.ingestao.config import get_ingestao_settings
    from app.ingestao.rastros import procurar_rastros

    with get_sessionmaker()() as session:
        achados = procurar_rastros(session, args.termo, get_ingestao_settings().arquivos_dir)
    if not achados:
        print(f"Nenhum rastro de {args.termo!r}: banco inteiro e pasta de arquivos limpos.")
        return 0
    print(f"Rastros de {args.termo!r}:")
    for a in achados:
        print(f"  - {a}")
    return 1


def _conferir(args: argparse.Namespace) -> int:
    """Confere se cada suporte do gabarito alcança algum trecho do corpus."""
    from sqlalchemy import text

    from app.avaliacao.conferencia import Linha, conferir
    from app.llm.tipos import TrechoRecuperado

    conjunto = carregar(args.conjunto)
    with get_sessionmaker()() as session:
        linhas = [
            Linha(TrechoRecuperado(r.id, r.nome, r.pagina, r.texto), r.tipo)
            for r in session.execute(
                text(
                    "SELECT t.id, t.pagina, t.texto, t.tipo, d.nome FROM trecho t "
                    "JOIN documento d ON d.id = t.documento_id"
                )
            )
        ]
    problemas = 0
    for caso_id, n, ok, mensagem in conferir(conjunto.casos, linhas):
        problemas += not ok
        print(f"{'OK  ' if ok else 'ERRO'} {caso_id} (suporte {n}): {mensagem}")
    print(f"\n{problemas} suporte(s) com problema")
    return 1 if problemas else 0


def _falhas(args: argparse.Namespace) -> int:
    """Resumo curto das perguntas cujo trecho correto não veio, a partir do .json da rodada."""
    dados = json.loads(args.resultado.read_text(encoding="utf-8"))
    modo = args.modo if args.modo in dados else next(iter(dados))
    casos = dados[modo]["casos"]
    for caso in casos:
        if caso["responde"] and caso["posicao"] is not None and not caso["tem_evidencia"]:
            nota = caso["topo"][0].get("nota_reranker") if caso["topo"] else None
            print(f"RECUSADA POR ENGANO (trecho certo na posição {caso['posicao']}, "
                  f"nota do 1º = {nota}): {caso['id']}: {caso['pergunta']}")  # fmt: skip
    for caso in casos:
        if not caso["responde"] or caso["posicao"] is not None:
            continue
        print(f"\n## {caso['id']}: {caso['pergunta']}")
        for n, t in enumerate(caso["topo"][: args.topo], start=1):
            texto = " ".join(t["texto"].split())[:150]
            print(f"  [{n}] {t['documento'][:40]} p.{t['pagina']} sim={t['similaridade']}: {texto}")
    return 0


def _json(rel: RelatorioModo, resumo: dict, limiares: list[dict]) -> dict:
    return {
        "resumo": resumo,
        "limiar": limiares,
        "casos": [
            {
                "id": r.caso.id,
                "pergunta": r.caso.pergunta,
                "responde": r.caso.responde,
                "resposta_esperada": r.caso.resposta_esperada,
                "posicao": r.posicao,
                "melhor_similaridade": r.melhor_similaridade,
                "ha_lexical": r.ha_lexical,
                "tem_evidencia": r.tem_evidencia,
                "latencia_busca_ms": r.latencia_busca_ms,
                "topo": r.topo,
                "resposta": r.resposta,
            }
            for r in rel.casos
        ],
    }


def _rodar(args: argparse.Namespace) -> int:
    try:
        conjunto = carregar(args.conjunto)
    except (ValidationError, json.JSONDecodeError, OSError) as erro:
        print(f"Conjunto inválido ({args.conjunto}):", file=sys.stderr)
        if isinstance(erro, ValidationError):
            for e in erro.errors():
                onde = ".".join(str(p) for p in e["loc"])
                print(f"  - {onde}: {e['msg']}", file=sys.stderr)
        else:
            print(f"  - {erro}", file=sys.stderr)
        return 2
    ajustes = get_consulta_settings()
    limiar = args.limiar if args.limiar is not None else ajustes.limiar_similaridade
    modos = [m.strip() for m in args.modos.split(",") if m.strip()]
    invalidos = [m for m in modos if m not in MODOS]
    if invalidos:
        print(f"Modo desconhecido: {invalidos}. Use {MODOS}", file=sys.stderr)
        return 2
    ks = tuple(int(k) for k in args.ks.split(","))
    embeddings = criar_embeddings()
    gerador = criar_gerador_de_resposta() if args.gerar else None
    reordenador = criar_reordenador(args.reranker or "", get_ingestao_settings().modelos_dir)

    resumos, detalhes, relatorios = [], {}, []
    with get_sessionmaker()() as session:
        for modo in modos:
            rotulo = args.rotulo or modo
            print(f"→ {rotulo} ({modo}): {len(conjunto.casos)} perguntas", file=sys.stderr)
            rel = avaliar(
                session,
                embeddings,
                conjunto.casos,
                rotulo=rotulo,
                modo=modo,  # type: ignore[arg-type]
                k=max(ks),
                k_resposta=ajustes.consulta_k,
                candidatos=ajustes.consulta_candidatos,
                rrf_k=ajustes.consulta_rrf_k,
                limiar=limiar,
                colecoes=args.colecoes,
                # a geração é cara: só roda no modo escolhido para ela (padrão: híbrida)
                gerador=gerador if modo == args.modo_geracao else None,
                reordenador=reordenador,
                candidatos_reranker=args.candidatos_reranker,
                nota_minima=(
                    args.nota_minima
                    if args.nota_minima is not None
                    else ajustes.reranker_nota_minima
                ),
            )
            resumo = relatorio.resumir(rel, ks)
            resumos.append(resumo)
            relatorios.append(rel)
            detalhes[modo] = _json(rel, resumo, relatorio.varrer_limiar(rel))
            detalhes[modo]["nota_reranker"] = relatorio.varrer_nota_reranker(rel)

    markdown = [f"# Avaliação {datetime.now():%Y-%m-%d %H:%M}", ""]
    markdown += ["## Recuperação", relatorio.tabela_recuperacao(resumos), ""]
    markdown += ["## Portão de evidência (efetivo)", relatorio.tabela_portao(relatorios), ""]
    geracao = relatorio.tabela_geracao(resumos)
    if geracao:
        markdown += ["## Geração", geracao, ""]
    base = next((r for r in relatorios if r.modo == "hibrida"), relatorios[0])
    markdown += [f"## Limiar de similaridade ({base.modo})"]
    markdown += [relatorio.tabela_limiar(relatorio.varrer_limiar(base)), ""]
    notas = relatorio.tabela_nota_reranker(relatorio.varrer_nota_reranker(base))
    if notas:
        markdown += [f"## Nota do reordenador como portão ({base.modo})", notas, ""]
    falhas = relatorio.casos_com_falha(base)
    if falhas:
        markdown += [f"## Perguntas cujo trecho correto não veio ({base.modo})"]
        markdown += [f"- `{r.caso.id}`: {r.caso.pergunta}" for r in falhas]
    texto = "\n".join(markdown)
    print(texto)

    args.saida.mkdir(parents=True, exist_ok=True)
    nome = f"{datetime.now():%Y%m%d-%H%M%S}-{args.rotulo or 'modos'}"
    (args.saida / f"{nome}.json").write_text(
        json.dumps(detalhes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.saida / f"{nome}.md").write_text(texto + "\n", encoding="utf-8")
    print(f"\nSalvo em {args.saida / nome}.json e .md", file=sys.stderr)
    return 0


def _embeddings(args: argparse.Namespace) -> int:
    """Compara modelos de embeddings (recuperação) sem alterar o banco."""
    import numpy as np

    from app.avaliacao import embeddings as exp
    from app.llm.config import get_llm_settings
    from app.llm.perfis import perfil_de

    try:
        conjunto = carregar(args.conjunto)
    except (ValidationError, json.JSONDecodeError, OSError) as erro:
        print(f"Conjunto inválido ({args.conjunto}): {erro}", file=sys.stderr)
        return 2
    modelos = [m.strip() for m in args.modelos.split(",") if m.strip()]
    modelos_dir = get_ingestao_settings().modelos_dir
    atual = get_llm_settings().embeddings_model
    resultados = []
    with get_sessionmaker()() as session:
        corpus = exp.carregar_corpus(session, conjunto.casos)
        print(f"Corpus do gabarito: {len(corpus.linhas)} trechos", file=sys.stderr)
        for nome in modelos:
            perfil = perfil_de(nome)
            print(
                f"→ {nome} ({perfil.dimensao or '?'} dim, {perfil.tamanho_gb or '?'} GB) "
                f"{perfil.observacao}",
                file=sys.stderr,
            )
            if nome == atual:  # os vetores já estão no banco: não recalcula
                ids = [linha_.id for linha_ in corpus.linhas]
                linhas = session.execute(
                    text("SELECT id, vetor::text FROM trecho WHERE id = ANY(:ids)"), {"ids": ids}
                ).all()
                por_id = {i: np.array(json.loads(v), dtype=np.float32) for i, v in linhas}
                matriz = np.vstack([por_id[i] for i in ids])
                matriz /= np.maximum(np.linalg.norm(matriz, axis=1, keepdims=True), 1e-9)
                velocidade = None
            else:
                matriz, velocidade = exp.vetores_do_corpus(
                    nome, corpus, args.cache, modelos_dir, session
                )
            resultados.append(
                exp.avaliar_modelo(
                    session, nome, corpus, matriz, conjunto.casos, modelos_dir,
                    trechos_por_s=velocidade,
                )
            )  # fmt: skip
    saida = [
        f"# Modelos de embeddings, {len(conjunto.casos)} perguntas, {len(corpus.linhas)} trechos\n",
        "## Busca híbrida (vetorial + textual, RRF)\n",
        exp.tabela(resultados, "hibrida"),
        "\n## Só vetorial\n",
        exp.tabela(resultados, "vetorial"),
        "\n## Posição do trecho certo, por pergunta (híbrida)\n",
        exp.tabela_por_caso(resultados, conjunto.casos),
    ]
    relatorio_md = "\n".join(saida)
    args.saida.mkdir(parents=True, exist_ok=True)
    arquivo = args.saida / f"{datetime.now():%Y%m%d-%H%M%S}-embeddings.md"
    arquivo.write_text(relatorio_md, encoding="utf-8")
    print(relatorio_md)
    print(f"\nSalvo em {arquivo}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.avaliacao", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    insp = sub.add_parser("inspecionar", help="mostra os trechos que a busca devolve")
    insp.add_argument("pergunta")
    insp.add_argument("--k", type=int, default=10)
    insp.add_argument("--colecoes", nargs="*")
    insp.set_defaults(funcao=_inspecionar)

    ras = sub.add_parser(
        "rastros", help="procura um texto no banco e nos arquivos (prova da remoção)"
    )
    ras.add_argument("termo", help="trecho do documento removido (4+ caracteres)")
    ras.set_defaults(funcao=_rastros)

    conf = sub.add_parser("conferir", help="confere se o gabarito alcança trechos do corpus")
    conf.add_argument("--conjunto", type=Path, default=Path("avaliacao/perguntas.json"))
    conf.set_defaults(funcao=_conferir)

    fal = sub.add_parser("falhas", help="resume as perguntas que falharam num .json de rodada")
    fal.add_argument("resultado", type=Path)
    fal.add_argument("--modo", default="hibrida")
    fal.add_argument("--topo", type=int, default=3)
    fal.set_defaults(funcao=_falhas)

    emb = sub.add_parser("embeddings", help="compara modelos de embeddings sem alterar o banco")
    emb.add_argument("--conjunto", type=Path, default=Path("avaliacao/perguntas.json"))
    emb.add_argument(
        "--modelos",
        default=(
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2,"
            "intfloat/multilingual-e5-large"
        ),
        help="nomes do fastembed, em vírgulas (veja app/llm/perfis.py)",
    )
    emb.add_argument("--cache", type=Path, default=Path("avaliacao/embeddings_cache"))
    emb.add_argument("--saida", type=Path, default=Path("avaliacao/resultados"))
    emb.set_defaults(funcao=_embeddings)

    rod = sub.add_parser("rodar", help="roda o conjunto de referência")
    rod.add_argument("--conjunto", type=Path, default=Path("avaliacao/perguntas.json"))
    rod.add_argument("--modos", default="hibrida,vetorial,textual")
    rod.add_argument("--ks", default="1,3,5,10,20", help="valores de k para o recall, em vírgulas")
    rod.add_argument("--gerar", action="store_true", help="também gera as respostas (usa o LLM)")
    rod.add_argument("--modo-geracao", default="hibrida", choices=MODOS)
    rod.add_argument("--rotulo", help="nome da variação, para a tabela de experimentos")
    rod.add_argument("--limiar", type=float, help="padrão: LIMIAR_SIMILARIDADE do .env")
    rod.add_argument("--colecoes", nargs="*")
    rod.add_argument(
        "--reranker",
        help="modelo do fastembed para reordenar (ex.: jinaai/jina-reranker-v2-base-multilingual)",
    )
    rod.add_argument("--candidatos-reranker", type=int, default=20)
    rod.add_argument(
        "--nota-minima",
        type=float,
        help="com --reranker: nota mínima do 1º trecho para haver evidência (o portão)",
    )
    rod.add_argument("--saida", type=Path, default=Path("avaliacao/resultados"))
    rod.set_defaults(funcao=_rodar)

    args = parser.parse_args(argv)
    return args.funcao(args)


if __name__ == "__main__":
    raise SystemExit(main())
