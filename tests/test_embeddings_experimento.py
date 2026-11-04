"""Experimento de embeddings (em memória) e prefixos por modelo."""

import numpy as np
import pytest
from auxiliares import ambiente, banco, precisa_de_banco  # noqa: F401

from app.avaliacao import embeddings as exp
from app.avaliacao.conjunto import Caso, Suporte
from app.ingestao import servico
from app.ingestao.config import get_ingestao_settings
from app.llm.local import EmbeddingsLocal
from app.llm.perfis import PERFIS, perfil_de
from app.llm.simulado import EmbeddingsSimulado


class ModeloFalso:
    """Imita o TextEmbedding do fastembed e guarda o que recebeu."""

    def __init__(self, dim: int = 384) -> None:
        self.simulado = EmbeddingsSimulado(dim)
        self.recebidos: list[str] = []
        self.tarefas: list[str] = []

    def embed(self, textos, batch_size=32):
        self.recebidos += list(textos)
        for v in self.simulado.gerar_trechos(list(textos)):
            yield np.array(v)

    def query_embed(self, textos, batch_size=32):
        self.tarefas.append("consulta")
        yield from self.embed(textos)

    def passage_embed(self, textos, batch_size=32):
        self.tarefas.append("trecho")
        yield from self.embed(textos)


def test_perfis_dizem_dimensao_e_prefixo() -> None:
    e5 = perfil_de("intfloat/multilingual-e5-large")
    assert (
        e5.dimensao == 1024
        and e5.prefixo_consulta == "query: "
        and e5.prefixo_trecho == "passage: "
    )
    assert perfil_de("modelo/desconhecido").prefixo_consulta == ""
    assert PERFIS["jinaai/jina-embeddings-v3"].tarefas
    assert PERFIS["jinaai/jina-embeddings-v3"].licenca == "cc-by-nc-4.0"


def test_embeddings_local_aplica_o_prefixo_certo() -> None:
    emb = EmbeddingsLocal("intfloat/multilingual-e5-large", 384)
    falso = ModeloFalso()
    emb._modelo = falso
    emb.gerar_trechos(["o prazo é de trinta dias"])
    emb.gerar_consulta("qual o prazo?")
    assert falso.recebidos == ["passage: o prazo é de trinta dias", "query: qual o prazo?"]


def test_modelo_sem_prefixo_recebe_o_texto_puro() -> None:
    emb = EmbeddingsLocal("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 384)
    falso = ModeloFalso()
    emb._modelo = falso
    emb.gerar_consulta("qual o prazo?")
    assert falso.recebidos == ["qual o prazo?"]


def test_jina_v3_escolhe_a_tarefa() -> None:
    emb = EmbeddingsLocal("jinaai/jina-embeddings-v3", 384)
    falso = ModeloFalso()
    emb._modelo = falso
    emb.gerar_trechos(["texto"])
    emb.gerar_consulta("pergunta")
    assert falso.tarefas == ["trecho", "consulta"]
    assert falso.recebidos == ["texto", "pergunta"]  # sem prefixo de texto


DOCUMENTOS = {
    "entrega.txt": "O prazo de entrega é de trinta dias úteis após o pagamento do pedido.",
    "garantia.txt": "A garantia cobre defeitos de fabricação por doze meses a partir da compra.",
    "bolo.txt": "A receita de bolo de cenoura leva cenouras, farinha, ovos e açúcar refinado.",
}
CASOS = [
    Caso(
        id="prazo",
        pergunta="Qual é o prazo de entrega?",
        suporte=[Suporte(documento="entrega", contem=["prazo"])],
    ),
    Caso(
        id="garantia",
        pergunta="Quanto tempo dura a garantia?",
        suporte=[Suporte(documento="garantia", contem=["garantia"])],
    ),
    Caso(id="fora", pergunta="Quem ganhou a copa de 1970?", responde=False),
]


@pytest.fixture
def corpus_db(banco):  # noqa: F811
    emb = EmbeddingsSimulado(768)
    for nome, conteudo in DOCUMENTOS.items():
        with banco() as s:
            reg = servico.registrar(
                s, nome=nome, conteudo=conteudo.encode(), colecao="c",
                config=get_ingestao_settings(),
            )  # fmt: skip
        servico.processar(banco, reg.documento_id, emb, get_ingestao_settings())
    return banco


@precisa_de_banco
def test_corpus_so_traz_documentos_do_gabarito(corpus_db) -> None:
    with corpus_db() as s:
        corpus = exp.carregar_corpus(s, CASOS)
    assert {x.nome for x in corpus.linhas} == {"entrega.txt", "garantia.txt"}  # bolo fica de fora


@precisa_de_banco
def test_vetores_com_cache_e_retomada(corpus_db, tmp_path, monkeypatch) -> None:
    falso = ModeloFalso()
    monkeypatch.setattr(exp, "_carregar_modelo", lambda nome, pasta: falso)
    with corpus_db() as s:
        corpus = exp.carregar_corpus(s, CASOS)
    m1, velocidade = exp.vetores_do_corpus(
        "intfloat/multilingual-e5-large", corpus, tmp_path, tmp_path, None
    )
    assert m1.shape == (len(corpus.linhas), 384) and velocidade
    assert all(t.startswith("passage: ") for t in falso.recebidos)  # prefixo do perfil
    falso.recebidos.clear()
    m2, de_cache = exp.vetores_do_corpus(
        "intfloat/multilingual-e5-large", corpus, tmp_path, tmp_path, None
    )
    assert de_cache is None and falso.recebidos == [] and np.allclose(m1, m2)
    assert not list(tmp_path.glob("*.parcial.npy"))


@precisa_de_banco
def test_avaliacao_acha_o_trecho_certo_e_monta_as_tabelas(corpus_db, tmp_path, monkeypatch) -> None:
    falso = ModeloFalso()
    monkeypatch.setattr(exp, "_carregar_modelo", lambda nome, pasta: falso)
    nome = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    with corpus_db() as s:
        corpus = exp.carregar_corpus(s, CASOS)
        matriz, _ = exp.vetores_do_corpus(nome, corpus, tmp_path, tmp_path, None)
        r = exp.avaliar_modelo(s, nome, corpus, matriz, CASOS, tmp_path)
    assert set(r.posicoes_hibrida) == {"prazo", "garantia"}  # a sem resposta não entra
    assert r.posicoes_hibrida["prazo"] == 1 and r.posicoes_vetorial["prazo"] == 1
    t = exp.tabela([r], "hibrida")
    assert "R@1" in t and "100%" in t and nome in t
    assert "prazo" in exp.tabela_por_caso([r], CASOS) and "fora" not in exp.tabela_por_caso(
        [r], CASOS
    )
