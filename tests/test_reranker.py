"""Reordenação: a função pura, a consulta com reordenador e a avaliação com e sem ele."""

import pytest
from auxiliares import ambiente, banco, precisa_de_banco  # noqa: F401

from app.avaliacao import relatorio
from app.avaliacao.conjunto import Caso, Suporte
from app.avaliacao.executor import avaliar
from app.consulta import reranker, servico
from app.consulta.busca import Achado
from app.consulta.config import ConsultaSettings
from app.ingestao import servico as ingestao
from app.ingestao.config import get_ingestao_settings
from app.llm.config import LLMSettings
from app.llm.simulado import EmbeddingsSimulado, GeradorDeRespostaSimulado
from app.llm.tipos import TrechoRecuperado


def _achado(i: int, texto: str) -> Achado:
    return Achado(
        trecho=TrechoRecuperado(i, "a.txt", 1, texto),
        documento_id=1,
        posicao=i,
        similaridade=0.5,
        rank_vetorial=i,
        rank_textual=None,
        pontuacao=1 / i,
    )


def test_reordenar_poe_o_mais_relevante_primeiro_e_guarda_a_nota() -> None:
    achados = [
        _achado(1, "nada a ver"),
        _achado(2, "prazo de entrega em dias"),
        _achado(3, "prazo"),
    ]
    saida = reranker.reordenar("prazo de entrega", achados, reranker.ReordenadorSimulado())
    assert [a.trecho.id for a in saida] == [2, 3, 1]
    assert saida[0].nota_reranker == pytest.approx(1.0)
    assert achados[0].nota_reranker is None  # o original não é alterado


def test_reordenar_e_estavel_no_empate() -> None:
    achados = [_achado(i, "texto igual") for i in (1, 2, 3)]
    saida = reranker.reordenar("outra coisa", achados, reranker.ReordenadorSimulado())
    assert [a.trecho.id for a in saida] == [1, 2, 3]


def test_criar_reordenador() -> None:
    assert reranker.criar_reordenador("") is None
    assert reranker.criar_reordenador("  ") is None
    assert isinstance(reranker.criar_reordenador("simulado"), reranker.ReordenadorSimulado)
    assert isinstance(reranker.criar_reordenador("BAAI/x"), reranker.ReordenadorLocal)


def test_config_valida_candidatos() -> None:
    assert ConsultaSettings(reranker_candidatos=20).reranker_candidatos == 20
    with pytest.raises(ValueError, match="RERANKER_CANDIDATOS"):
        ConsultaSettings(consulta_k=8, reranker_candidatos=5)
    with pytest.raises(ValueError, match="RERANKER_CANDIDATOS"):
        ConsultaSettings(reranker_candidatos=51)


class _Invertido:
    """Reordenador de teste: dá a maior nota ao último candidato."""

    nome_modelo = "invertido"

    def pontuar(self, pergunta, textos):
        return [float(i) for i, _ in enumerate(textos)]


DOCUMENTOS = {
    "entrega.txt": "O prazo de entrega é de trinta dias úteis após o pagamento.",
    "garantia.txt": "A garantia cobre defeitos de fabricação por doze meses.",
    "bolo.txt": "A receita de bolo de cenoura leva cenouras, farinha e açúcar.",
}


@pytest.fixture
def corpus(banco):  # noqa: F811
    emb = EmbeddingsSimulado(384)
    for nome, conteudo in DOCUMENTOS.items():
        with banco() as s:
            reg = ingestao.registrar(
                s, nome=nome, conteudo=conteudo.encode(), colecao="geral",
                config=get_ingestao_settings(),
            )  # fmt: skip
        ingestao.processar(banco, reg.documento_id, emb, get_ingestao_settings())
    return banco, emb


@precisa_de_banco
def test_consulta_usa_o_reordenador_e_respeita_k(corpus) -> None:
    sessoes, emb = corpus
    with sessoes() as s:
        resultado = servico.perguntar(
            s,
            "Qual é o prazo de entrega?",
            embeddings=emb,
            gerador=GeradorDeRespostaSimulado(),
            llm=LLMSettings(),
            consulta=ConsultaSettings(consulta_k=2),
            reordenador=_Invertido(),
        )
    usados = resultado.trechos_usados
    assert len(usados) == 2  # cortou em k depois de reordenar
    assert usados[0]["documento"] != "entrega.txt"  # o reordenador mandou, não a busca


@precisa_de_banco
def test_avaliar_com_reordenador_registra_notas(corpus) -> None:
    sessoes, emb = corpus
    casos = [
        Caso(
            id="prazo",
            pergunta="Qual é o prazo de entrega?",
            suporte=[Suporte(documento="entrega", contem=["trinta dias"])],
        ),
        Caso(id="fora", pergunta="Qual a capital da Austrália?", responde=False),
    ]
    with sessoes() as s:
        sem = avaliar(s, emb, casos, rotulo="sem", k=3)
        com = avaliar(s, emb, casos, rotulo="com", k=3, reordenador=reranker.ReordenadorSimulado())
    assert relatorio.varrer_nota_reranker(sem) == []
    assert com.casos[0].posicao == 1
    assert com.casos[0].melhor_nota_reranker is not None
    linhas = relatorio.varrer_nota_reranker(com)
    assert linhas and relatorio.melhor_limiar_reranker(linhas) is not None
    assert "Nota mínima" in relatorio.tabela_nota_reranker(linhas)


@precisa_de_banco
def test_nota_minima_recusa_sem_chamar_o_llm(corpus) -> None:
    sessoes, emb = corpus
    consulta = ConsultaSettings(consulta_k=2, reranker_nota_minima=0.5)

    def perguntar(pergunta: str):
        with sessoes() as s:
            return servico.perguntar(
                s,
                pergunta,
                embeddings=emb,
                gerador=GeradorDeRespostaSimulado(),
                llm=LLMSettings(),
                consulta=consulta,
                reordenador=reranker.ReordenadorSimulado(),
            )

    assert perguntar("prazo de entrega dias").resposta.recusou is False
    assert perguntar("capital Austrália governo").resposta.recusou is True


@precisa_de_banco
def test_avaliar_com_nota_minima_decide_o_portao(corpus) -> None:
    sessoes, emb = corpus
    casos = [
        Caso(
            id="prazo",
            pergunta="prazo de entrega dias",
            suporte=[Suporte(documento="entrega", contem=["trinta dias"])],
        ),
        Caso(id="fora", pergunta="capital Austrália governo", responde=False),
    ]
    with sessoes() as s:
        rel = avaliar(
            s, emb, casos, rotulo="x", k=3, reordenador=reranker.ReordenadorSimulado(),
            nota_minima=0.5,
        )  # fmt: skip
    por_id = {r.caso.id: r for r in rel.casos}
    assert por_id["prazo"].tem_evidencia is True
    assert por_id["fora"].tem_evidencia is False
    tabela = relatorio.tabela_portao([rel])
    assert "nota do reordenador >= 0.5" in tabela and "100%" in tabela
