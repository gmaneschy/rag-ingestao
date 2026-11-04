"""Busca híbrida: a fusão (unitária) e a busca de verdade contra o Postgres."""

from types import SimpleNamespace

import pytest
from auxiliares import ambiente, banco, precisa_de_banco  # noqa: F401
from sqlalchemy import text

from app.consulta import busca
from app.ingestao import servico
from app.ingestao.config import get_ingestao_settings
from app.llm.simulado import EmbeddingsSimulado


def _linha(i: int, sim: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=i,
        posicao=i,
        pagina=1,
        secao=None,
        texto=f"t{i}",
        documento_id=1,
        nome="a.txt",
        similaridade=sim,
    )


def test_fusao_premia_quem_aparece_nas_duas_listas() -> None:
    vetoriais = [_linha(1, 0.9), _linha(2, 0.8), _linha(3, 0.7)]
    textuais = [_linha(3), _linha(4)]
    achados = busca.fundir(vetoriais, textuais, rrf_k=60, k=10)
    # 3 está nas duas listas (1/63 + 1/61) e passa o 1, que só está na vetorial (1/61).
    # 2 e 4 empatam em 1/62; o desempate é a similaridade (0,8 contra nenhuma).
    assert [a.trecho.id for a in achados] == [3, 1, 2, 4]
    por_id = {a.trecho.id: a for a in achados}
    assert por_id[3].rank_vetorial == 3 and por_id[3].rank_textual == 1
    assert por_id[3].pontuacao == pytest.approx(1 / 63 + 1 / 61)
    assert por_id[4].similaridade is None and por_id[4].rank_vetorial is None
    assert por_id[1].similaridade == 0.9
    assert por_id[3].pontuacao > por_id[2].pontuacao


def test_fusao_respeita_k_e_desempata_pela_similaridade() -> None:
    achados = busca.fundir([_linha(1, 0.5), _linha(2, 0.9)], [_linha(2), _linha(1)], rrf_k=60, k=1)
    assert len(achados) == 1
    assert achados[0].trecho.id == 2  # mesma nota nos dois; desempata a maior similaridade


def test_fusao_de_listas_vazias() -> None:
    assert busca.fundir([], [], rrf_k=60, k=5) == []


def test_tem_evidencia() -> None:
    def achado(sim, rank_textual):
        return busca.Achado(
            trecho=SimpleNamespace(), documento_id=1, posicao=0, similaridade=sim,
            rank_vetorial=1 if sim is not None else None, rank_textual=rank_textual, pontuacao=1.0,
        )  # fmt: skip

    assert busca.tem_evidencia([achado(0.1, 1)], 0.25)  # palavra em comum basta
    assert busca.tem_evidencia([achado(0.3, None)], 0.25)  # parecido o bastante
    assert not busca.tem_evidencia([achado(0.1, None)], 0.25)
    assert not busca.tem_evidencia([], 0.25)


pytestmark_db = precisa_de_banco

DOCUMENTOS = {
    ("entrega.txt", "contratos"): "O prazo de entrega é de trinta dias úteis após o pagamento.",
    ("garantia.txt", "contratos"): "A garantia cobre defeitos de fabricação por doze meses.",
    ("bolo.txt", "cozinha"): "A receita de bolo de cenoura leva cenouras, farinha e açúcar.",
}


@pytest.fixture
def corpus(banco):  # noqa: F811
    emb = EmbeddingsSimulado(768)
    for (nome, colecao), conteudo in DOCUMENTOS.items():
        with banco() as s:
            reg = servico.registrar(
                s,
                nome=nome,
                conteudo=conteudo.encode(),
                colecao=colecao,
                config=get_ingestao_settings(),
            )
        servico.processar(banco, reg.documento_id, emb, get_ingestao_settings())
    return banco, emb


def _buscar(corpus, pergunta: str, **kw):
    banco_, emb = corpus
    with banco_() as s:
        vetor = emb.gerar_consulta(pergunta) if kw.get("modo") != "textual" else None
        return busca.buscar(s, pergunta, vetor, **kw)


@precisa_de_banco
def test_busca_hibrida_poe_o_trecho_certo_em_primeiro(corpus) -> None:
    achados = _buscar(corpus, "Qual é o prazo de entrega?")
    assert achados[0].trecho.documento == "entrega.txt"
    assert achados[0].rank_vetorial == 1 and achados[0].rank_textual == 1


@precisa_de_banco
def test_busca_textual_usa_o_radical_em_portugues(corpus) -> None:
    achados = _buscar(corpus, "entregas", modo="textual")
    assert [a.trecho.documento for a in achados] == ["entrega.txt"]
    assert achados[0].similaridade is None


@precisa_de_banco
def test_busca_textual_aceita_qualquer_palavra_da_pergunta(corpus) -> None:
    achados = _buscar(corpus, "garantia de defeitos ou receita de bolo", modo="textual")
    assert {a.trecho.documento for a in achados} == {"garantia.txt", "bolo.txt"}


@precisa_de_banco
def test_busca_vetorial_devolve_a_similaridade(corpus) -> None:
    achados = _buscar(corpus, "garantia de fabricação", modo="vetorial")
    assert achados[0].trecho.documento == "garantia.txt"
    assert achados[0].similaridade > 0.3 and achados[0].rank_textual is None


@precisa_de_banco
def test_filtro_de_colecao_vale_para_as_duas_buscas(corpus) -> None:
    for modo in ("hibrida", "vetorial", "textual"):
        achados = _buscar(
            corpus, "bolo de cenoura e prazo de entrega", modo=modo, colecoes=["cozinha"]
        )
        assert {a.trecho.documento for a in achados} <= {"bolo.txt"}, modo
    assert _buscar(corpus, "prazo de entrega", colecoes=["inexistente"]) == []


@precisa_de_banco
def test_pergunta_so_com_palavras_vazias_nao_quebra_a_busca_textual(corpus) -> None:
    assert _buscar(corpus, "o de que", modo="textual") == []


@precisa_de_banco
def test_k_limita_o_resultado(corpus) -> None:
    assert len(_buscar(corpus, "prazo garantia bolo", k=2)) == 2


@precisa_de_banco
def test_verificar_modelo_aceita_o_mesmo_e_recusa_outro(corpus) -> None:
    banco_, emb = corpus
    with banco_() as s:
        busca.verificar_modelo(s, emb.nome_modelo, emb.dimensao, None)
        s.execute(
            text("UPDATE trecho SET modelo_embedding = 'outro-modelo' WHERE texto LIKE '%bolo%'")
        )
        s.commit()
        busca.verificar_modelo(s, emb.nome_modelo, emb.dimensao, ["contratos"])  # só vê contratos
        with pytest.raises(busca.ReindexacaoNecessaria) as erro:
            busca.verificar_modelo(s, emb.nome_modelo, emb.dimensao, None)
    assert erro.value.documentos == ["bolo.txt"]


@pytest.fixture
def corpus_ingles(banco):  # noqa: F811
    emb = EmbeddingsSimulado(768)
    textos = {
        f"comum{i}.txt": f"What was said on Tuesday was long, and the report was dull {i}."
        for i in range(24)
    }
    textos["rara.txt"] = "Zorblax gave a concise opinion about the budget."
    for nome, conteudo in textos.items():
        with banco() as s:
            reg = servico.registrar(
                s, nome=nome, conteudo=conteudo.encode(), colecao="en",
                config=get_ingestao_settings(),
            )  # fmt: skip
        servico.processar(banco, reg.documento_id, emb, get_ingestao_settings())
    return banco, emb


@precisa_de_banco
def test_busca_textual_ignora_palavras_comuns_demais_do_corpus(corpus_ingles) -> None:
    sessoes, emb = corpus_ingles
    with sessoes() as s:
        achados = busca.buscar(s, "What was Zorblax opinion?", None, modo="textual", k=5)
    # "what" e "was" estão em quase todos os trechos: só a palavra rara conta.
    assert [a.trecho.documento for a in achados] == ["rara.txt"]


@precisa_de_banco
def test_busca_textual_sem_termo_raro_nao_devolve_nada(corpus_ingles) -> None:
    sessoes, emb = corpus_ingles
    with sessoes() as s:
        achados = busca.buscar(s, "What was on the report?", None, modo="textual", k=5)
    assert achados == []  # só palavras comuns demais: o que decide é a busca vetorial


@pytest.fixture
def corpus_com_documentos_distantes(banco):  # noqa: F811
    """Documentos A (muitos, parecidos com a pergunta) e B (muitos, longe dela)."""
    emb = EmbeddingsSimulado(768)
    textos = {
        f"a{i}.txt": f"O prazo de entrega do pedido {i} é de trinta dias úteis para o cliente."
        for i in range(80)
    }
    textos.update(
        {
            f"b{i}.txt": "Zorblax quimera sinfonia nebulosa. "
            + " ".join(f"lume{i}x{j}" for j in range(10))
            for i in range(80)
        }
    )
    for nome, conteudo in textos.items():
        with banco() as s:
            reg = servico.registrar(
                s, nome=nome, conteudo=conteudo.encode(), colecao="x",
                config=get_ingestao_settings(),
            )  # fmt: skip
        servico.processar(banco, reg.documento_id, emb, get_ingestao_settings())
    return banco, emb


@precisa_de_banco
def test_busca_vetorial_com_documentos_escolhidos_nao_depende_do_indice(
    corpus_com_documentos_distantes,
) -> None:
    sessoes, emb = corpus_com_documentos_distantes
    pergunta = "Qual é o prazo de entrega do pedido?"
    vetor = emb.gerar_consulta(pergunta)
    with sessoes() as s:
        ids_b = list(s.execute(text("SELECT id FROM documento WHERE nome LIKE 'b%'")).scalars())
        antigo = (
            s.execute(  # o índice entrega os ~40 vizinhos do corpus inteiro; o filtro vem depois
                text(
                    "SELECT t.id FROM (SELECT id, documento_id FROM trecho "
                    "ORDER BY vetor <=> CAST(:v AS vector) LIMIT 40) t "
                    "WHERE t.documento_id = ANY(:ids) LIMIT 5"
                ),
                {"ids": ids_b, "v": busca.vetor_como_texto(vetor)},
            ).all()
        )
        achados = busca.buscar(s, pergunta, vetor, modo="vetorial", k=5, documento_ids=ids_b)
    assert len(antigo) < 5  # o defeito: o índice entrega vizinhos de outros documentos
    assert len(achados) == 5 and all(a.trecho.documento.startswith("b") for a in achados)
