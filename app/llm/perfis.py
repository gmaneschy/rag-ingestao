"""Como cada modelo de embeddings quer receber o texto.

Vários modelos distinguem a pergunta do trecho guardado por um prefixo ("query: ", "passage: ").
Sem ele a qualidade cai de forma silenciosa, então o prefixo vive junto do nome do modelo e vale
igual no experimento (`python -m app.avaliacao embeddings`) e na ingestão e consulta de verdade.
O fastembed NÃO aplica esses prefixos sozinho, exceto no jina-embeddings-v3 (que escolhe a tarefa).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Perfil:
    dimensao: int
    prefixo_consulta: str = ""
    prefixo_trecho: str = ""
    tarefas: bool = False  # o próprio modelo distingue consulta e trecho (jina v3)
    tamanho_gb: float = 0.0
    licenca: str = ""
    observacao: str = ""


PERFIS: dict[str, Perfil] = {
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": Perfil(
        384, tamanho_gb=0.22, licenca="apache-2.0", observacao="atual; enxerga ~128 tokens"
    ),
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": Perfil(
        768, tamanho_gb=1.0, licenca="apache-2.0", observacao="mesma família, maior; 512 tokens"
    ),
    "intfloat/multilingual-e5-large": Perfil(
        1024,
        prefixo_consulta="query: ",
        prefixo_trecho="passage: ",
        tamanho_gb=2.24,
        licenca="mit",
        observacao="feito para busca; ~100 idiomas",
    ),
    "google/embeddinggemma-300m": Perfil(
        768,
        prefixo_consulta="task: search result | query: ",
        prefixo_trecho="title: none | text: ",
        tamanho_gb=1.24,
        licenca="gemma",
        observacao="300M parâmetros; 2048 tokens",
    ),
    "Qwen/Qwen3-Embedding-0.6B-Q": Perfil(
        1024,
        prefixo_consulta=(
            "Instruct: Given a question, retrieve passages that answer the question\nQuery: "
        ),
        tamanho_gb=1.12,
        licenca="apache-2.0",
        observacao="quantizado; 32k tokens",
    ),
    "jinaai/jina-embeddings-v3": Perfil(
        1024,
        tarefas=True,
        tamanho_gb=2.29,
        licenca="cc-by-nc-4.0",
        observacao="licença NÃO comercial",
    ),
}


def perfil_de(nome_modelo: str) -> Perfil:
    """O perfil do modelo; modelo desconhecido é tratado como sem prefixos."""
    return PERFIS.get(nome_modelo, Perfil(0))
