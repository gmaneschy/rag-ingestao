import json

import httpx
import pytest

from app.llm.ollama import GeradorDeRespostaOllama
from app.llm.tipos import MENSAGEM_SEM_EVIDENCIA, ModeloIndisponivel, TrechoRecuperado

TRECHOS = [
    TrechoRecuperado(1, "manual.pdf", 2, "O prazo de entrega é de trinta dias."),
    TrechoRecuperado(2, "manual.pdf", 5, "A garantia cobre defeitos por um ano."),
]


def _gerador(resposta, **kw) -> tuple[GeradorDeRespostaOllama, list[httpx.Request]]:
    pedidos: list[httpx.Request] = []

    def tratar(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        return resposta(pedido) if callable(resposta) else resposta

    cliente = httpx.Client(transport=httpx.MockTransport(tratar))
    return GeradorDeRespostaOllama(
        "http://ollama:11434/", "modelo-x", cliente=cliente, **kw
    ), pedidos


def _ok(conteudo: str, entrada: int = 120, saida: int = 20) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "message": {"role": "assistant", "content": conteudo},
            "prompt_eval_count": entrada,
            "eval_count": saida,
        },
    )


def test_resposta_com_citacao() -> None:
    gerador, pedidos = _gerador(_ok("O prazo é de trinta dias [1]."))
    resposta = gerador.responder("Qual o prazo?", TRECHOS)
    assert resposta.texto == "O prazo é de trinta dias [1]."
    assert resposta.fontes == (1,) and not resposta.recusou
    assert (resposta.tokens_entrada, resposta.tokens_saida, resposta.custo) == (120, 20, 0.0)
    assert gerador.nome_modelo == "ollama/modelo-x"
    assert str(pedidos[0].url) == "http://ollama:11434/api/chat"


def test_o_pedido_e_deterministico_e_sem_streaming() -> None:
    gerador, pedidos = _gerador(_ok("ok [1]"), max_tokens=300)
    gerador.responder("p?", TRECHOS)
    corpo = json.loads(pedidos[0].content)
    assert corpo["model"] == "modelo-x" and corpo["stream"] is False
    assert corpo["options"]["temperature"] == 0 and corpo["options"]["num_predict"] == 300
    assert [m["role"] for m in corpo["messages"]] == ["system", "user"]


def test_recusa_do_modelo_e_reconhecida_e_nao_cita() -> None:
    gerador, _ = _gerador(_ok(MENSAGEM_SEM_EVIDENCIA))
    resposta = gerador.responder("p?", TRECHOS)
    assert resposta.recusou and resposta.fontes == ()


def test_num_ctx_cresce_com_o_prompt_e_respeita_o_teto() -> None:
    gerador, pedidos = _gerador(_ok("ok"), num_ctx_max=8192)
    gerador.responder("p?", TRECHOS)
    assert json.loads(pedidos[0].content)["options"]["num_ctx"] == 4096  # mínimo

    enorme = [TrechoRecuperado(1, "a", 1, "palavra " * 6000)]
    gerador.responder("p?", enorme)
    assert json.loads(pedidos[1].content)["options"]["num_ctx"] == 8192  # teto

    pequeno, pedidos2 = _gerador(_ok("ok"), num_ctx_max=4096)
    pequeno.responder("p?", enorme)
    assert json.loads(pedidos2[0].content)["options"]["num_ctx"] == 4096


def test_bloco_de_raciocinio_e_removido() -> None:
    gerador, _ = _gerador(_ok("<think>hmm</think>Vale trinta dias [1]."))
    assert gerador.responder("p?", TRECHOS).texto == "Vale trinta dias [1]."


def test_modelo_inexistente_orienta_o_pull() -> None:
    gerador, _ = _gerador(httpx.Response(404, json={"error": "model 'modelo-x' not found"}))
    with pytest.raises(ModeloIndisponivel, match="ollama pull modelo-x"):
        gerador.responder("p?", TRECHOS)


def test_servidor_fora_do_ar() -> None:
    def recusa(pedido: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusado", request=pedido)

    gerador, _ = _gerador(recusa)
    with pytest.raises(ModeloIndisponivel, match="rodando"):
        gerador.responder("p?", TRECHOS)


def test_demora_demais() -> None:
    def lento(pedido: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=pedido)

    gerador, _ = _gerador(lento, timeout_s=7)
    with pytest.raises(ModeloIndisponivel, match="7s"):
        gerador.responder("p?", TRECHOS)


@pytest.mark.parametrize("resposta", [httpx.Response(500, text="erro"), _ok("   ")])
def test_erro_do_servidor_e_resposta_vazia(resposta) -> None:
    gerador, _ = _gerador(resposta)
    with pytest.raises(ModeloIndisponivel):
        gerador.responder("p?", TRECHOS)


def test_erro_do_ollama_inclui_o_motivo_devolvido() -> None:
    gerador, _ = _gerador(httpx.Response(400, json={"error": "modelo não suporta isto"}))
    with pytest.raises(ModeloIndisponivel, match="400.*modelo não suporta isto"):
        gerador.responder("p?", TRECHOS)
