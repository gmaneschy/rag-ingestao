import math

from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
from app.llm.simulado import EmbeddingsSimulado, GeradorDeRespostaSimulado
from app.llm.tipos import MENSAGEM_SEM_EVIDENCIA, TrechoRecuperado


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_implementacoes_cumprem_os_protocolos() -> None:
    assert isinstance(EmbeddingsSimulado(384), GeradorDeEmbeddings)
    assert isinstance(GeradorDeRespostaSimulado(), GeradorDeResposta)


def test_embedding_tem_a_dimensao_e_norma_unitaria() -> None:
    vetor = EmbeddingsSimulado(384).gerar_consulta("prazo de entrega do contrato")
    assert len(vetor) == 384
    assert math.isclose(math.sqrt(sum(v * v for v in vetor)), 1.0, rel_tol=1e-9)


def test_embedding_e_deterministico_e_ignora_acento_e_caixa() -> None:
    e = EmbeddingsSimulado(384)
    assert e.gerar_consulta("Atenção ao Prazo") == e.gerar_consulta("atencao ao prazo")


def test_textos_parecidos_ficam_mais_proximos_que_textos_distintos() -> None:
    e = EmbeddingsSimulado(384)
    base, parecido, outro = e.gerar_trechos(
        [
            "o prazo de entrega do contrato é de trinta dias",
            "qual o prazo de entrega no contrato",
            "receita de bolo de cenoura com cobertura",
        ]
    )
    assert _cos(base, parecido) > _cos(base, outro)


def test_texto_vazio_nao_quebra() -> None:
    assert EmbeddingsSimulado(8).gerar_consulta("") == [0.0] * 8


def _trecho(i: int, texto: str) -> TrechoRecuperado:
    return TrechoRecuperado(id=i, documento="a.pdf", pagina=i, texto=texto)


def test_resposta_cita_o_trecho_com_mais_palavras_em_comum() -> None:
    resposta = GeradorDeRespostaSimulado().responder(
        "qual o prazo de entrega?",
        [
            _trecho(1, "A garantia dura um ano."),
            _trecho(2, "O prazo de entrega é de 30 dias. Fim."),
        ],
    )
    assert not resposta.recusou
    assert resposta.fontes == (2,)
    assert resposta.texto == "O prazo de entrega é de 30 dias. [2]"
    assert resposta.tokens_entrada > 0 and resposta.tokens_saida > 0


def test_recusa_quando_nao_ha_evidencia() -> None:
    resposta = GeradorDeRespostaSimulado().responder(
        "quem ganhou a copa?", [_trecho(1, "A garantia dura um ano.")]
    )
    assert resposta.recusou
    assert resposta.fontes == ()
    assert resposta.texto == MENSAGEM_SEM_EVIDENCIA


def test_recusa_sem_nenhum_trecho() -> None:
    assert GeradorDeRespostaSimulado().responder("qualquer coisa", []).recusou
