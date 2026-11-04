"""Compara modelos de embeddings com o conjunto de referência, sem mexer no banco.

Os trechos `corpo` dos documentos do gabarito são lidos do banco e vetorizados na memória com cada
modelo candidato (o vetor do modelo em uso vem do próprio banco, sem recalcular). A busca é feita
em memória e a vertente textual vem do Postgres, como na consulta real; assim dá para medir
modelos de outra dimensão (768, 1024) antes de migrar a coluna `trecho.vetor`.

Os vetores ficam em disco (`avaliacao/embeddings_cache`), com retomada: interromper e rodar de
novo continua de onde parou. A medida é de RECUPERAÇÃO (o trecho certo entre os k primeiros).
A recusa sem resposta depende do reordenador e não muda com o modelo de embeddings.
"""

import hashlib
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.avaliacao import metricas
from app.avaliacao.conjunto import Caso
from app.consulta import busca
from app.llm.perfis import perfil_de
from app.llm.tipos import TrechoRecuperado

KS = (1, 3, 5, 10, 20)
LOTE = 64


@dataclass
class Corpus:
    linhas: list[SimpleNamespace]  # id, posicao, pagina, secao, texto, documento_id, nome, colecao

    def trechos(self) -> list[TrechoRecuperado]:
        return [
            TrechoRecuperado(linha_.id, linha_.nome, linha_.pagina, linha_.texto, linha_.secao)
            for linha_ in self.linhas
        ]


def carregar_corpus(session: Session, casos: list[Caso]) -> Corpus:
    """Trechos `corpo` dos documentos que algum caso do gabarito cita."""
    nomes = {metricas.normalizar_nome(s.documento) for c in casos for s in c.suporte}
    linhas = session.execute(
        text(
            "SELECT t.id, t.posicao, t.pagina, t.secao, t.texto, d.id AS documento_id, d.nome, "
            "d.colecao FROM trecho t JOIN documento d ON d.id = t.documento_id "
            "WHERE t.tipo = 'corpo' AND d.estado = 'processado' ORDER BY d.id, t.posicao"
        )
    ).all()
    escolhidas = [
        SimpleNamespace(**dict(linha_._mapping), similaridade=None)
        for linha_ in linhas
        if any(n in metricas.normalizar_nome(linha_.nome) for n in nomes)
    ]
    if not escolhidas:
        raise SystemExit("Nenhum trecho dos documentos do gabarito no banco. Rode `conferir`.")
    return Corpus(escolhidas)


def _slug(modelo: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", modelo).strip("-").lower()


def _carregar_modelo(nome: str, cache_dir: Path):
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=nome, cache_dir=str(cache_dir))


def _vetorizar(modelo, nome: str, textos: list[str], *, consulta: bool) -> np.ndarray:
    perfil = perfil_de(nome)
    prefixo = perfil.prefixo_consulta if consulta else perfil.prefixo_trecho
    entrada = [prefixo + t for t in textos] if prefixo else textos
    if perfil.tarefas:
        gerar = modelo.query_embed if consulta else modelo.passage_embed
        saida = list(gerar(entrada, batch_size=LOTE))
    else:
        saida = list(modelo.embed(entrada, batch_size=LOTE))
    matriz = np.asarray(saida, dtype=np.float32)
    return matriz / np.maximum(np.linalg.norm(matriz, axis=1, keepdims=True), 1e-9)


def vetores_do_corpus(
    modelo_nome: str, corpus: Corpus, pasta: Path, modelos_dir: Path, session: Session | None
) -> tuple[np.ndarray, float | None]:
    """(matriz normalizada, trechos por segundo ou None se veio do cache/banco)."""
    textos = [linha_.texto for linha_ in corpus.linhas]
    impressao = hashlib.sha1("\n".join(textos).encode()).hexdigest()[:12]
    arquivo = pasta / f"{_slug(modelo_nome)}-{impressao}.npy"
    parcial = pasta / f"{_slug(modelo_nome)}-{impressao}.parcial.npy"
    if arquivo.exists():
        return np.load(arquivo), None
    pasta.mkdir(parents=True, exist_ok=True)
    feitos = np.load(parcial) if parcial.exists() else np.zeros((0, 0), dtype=np.float32)
    modelo = _carregar_modelo(modelo_nome, modelos_dir)
    partes = [feitos] if len(feitos) else []
    n_feitos = len(feitos)
    inicio, processados = time.perf_counter(), 0
    for i in range(n_feitos, len(textos), LOTE):
        partes.append(_vetorizar(modelo, modelo_nome, textos[i : i + LOTE], consulta=False))
        processados += min(LOTE, len(textos) - i)
        np.save(parcial, np.vstack(partes))
        velocidade = processados / (time.perf_counter() - inicio)
        faltam = (len(textos) - i - LOTE) / velocidade
        print(
            f"  {modelo_nome}: {min(i + LOTE, len(textos))}/{len(textos)} "
            f"({velocidade:.1f} trechos/s, faltam ~{max(faltam, 0) / 60:.0f} min)",
            file=sys.stderr,
        )
    matriz = np.vstack(partes)
    np.save(arquivo, matriz)
    parcial.unlink(missing_ok=True)
    return matriz, processados / (time.perf_counter() - inicio)


def _linhas_da_ordem(corpus: Corpus, indices: np.ndarray, notas: np.ndarray) -> list:
    saida = []
    for i in indices:
        linha = corpus.linhas[int(i)]
        saida.append(SimpleNamespace(**{**vars(linha), "similaridade": float(notas[i])}))
    return saida


@dataclass
class ResultadoModelo:
    modelo: str
    dimensao: int
    trechos_por_s: float | None
    posicoes_vetorial: dict[str, int | None]
    posicoes_hibrida: dict[str, int | None]


def avaliar_modelo(
    session: Session,
    modelo_nome: str,
    corpus: Corpus,
    matriz: np.ndarray,
    casos: list[Caso],
    modelos_dir: Path,
    *,
    trechos_por_s: float | None = None,
) -> ResultadoModelo:
    modelo = _carregar_modelo(modelo_nome, modelos_dir)
    colecoes = np.array([linha_.colecao for linha_ in corpus.linhas])
    pos_v: dict[str, int | None] = {}
    pos_h: dict[str, int | None] = {}
    for caso in casos:
        if not caso.responde:
            continue
        consulta = _vetorizar(modelo, modelo_nome, [caso.pergunta], consulta=True)[0]
        notas = matriz @ consulta
        if caso.colecoes:
            notas = np.where(np.isin(colecoes, caso.colecoes), notas, -np.inf)
        ordem = np.argsort(-notas)[:20]
        vetoriais = _linhas_da_ordem(corpus, ordem, notas)
        textuais = busca._buscar_textual(
            session, caso.pergunta, 20, busca.Filtros(caso.colecoes, tipos=("corpo",))
        )
        pos_v[caso.id] = metricas.primeiro_acerto(
            caso.suporte,
            [
                TrechoRecuperado(linha_.id, linha_.nome, linha_.pagina, linha_.texto, linha_.secao)
                for linha_ in vetoriais
            ],
        )
        fundidos = busca.fundir(vetoriais, textuais, rrf_k=60, k=20)
        pos_h[caso.id] = metricas.primeiro_acerto(caso.suporte, [a.trecho for a in fundidos])
    return ResultadoModelo(modelo_nome, matriz.shape[1], trechos_por_s, pos_v, pos_h)


def _linha_de_metricas(posicoes: dict[str, int | None]) -> list[str]:
    valores = list(posicoes.values())
    return [f"{metricas.recall_em_k(valores, k):.0%}" for k in KS] + [
        f"{metricas.mrr(valores, 10):.3f}"
    ]


def tabela(resultados: list[ResultadoModelo], qual: str) -> str:
    cabeca = ["Modelo", "dim", *[f"R@{k}" for k in KS], "MRR@10", "trechos/s"]
    linhas = ["| " + " | ".join(cabeca) + " |", "|" + "---|" * len(cabeca)]
    for r in resultados:
        posicoes = r.posicoes_vetorial if qual == "vetorial" else r.posicoes_hibrida
        velocidade = f"{r.trechos_por_s:.1f}" if r.trechos_por_s else "—"
        linhas.append(
            "| "
            + " | ".join([r.modelo, str(r.dimensao), *_linha_de_metricas(posicoes), velocidade])
            + " |"
        )
    return "\n".join(linhas)


def tabela_por_caso(resultados: list[ResultadoModelo], casos: list[Caso]) -> str:
    """Posição do trecho certo (híbrida) por pergunta e modelo: mostra onde cada um erra."""
    nomes = [r.modelo.split("/")[-1] for r in resultados]
    cabeca = ["Caso", "Pergunta", *nomes]
    linhas = ["| " + " | ".join(cabeca) + " |", "|" + "---|" * len(cabeca)]
    for caso in casos:
        if not caso.responde:
            continue
        celulas = [str(r.posicoes_hibrida.get(caso.id) or "—") for r in resultados]
        linhas.append(f"| {caso.id} | {caso.pergunta[:60]} | " + " | ".join(celulas) + " |")
    return "\n".join(linhas)
