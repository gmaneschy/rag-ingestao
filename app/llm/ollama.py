"""Geração de resposta por um servidor Ollama (modelo local), pela API nativa /api/chat."""

import math
from collections.abc import Sequence

import httpx

from app.llm.prompt import e_recusa, extrair_citacoes, limpar_saida, montar_mensagens
from app.llm.tipos import ModeloIndisponivel, Resposta, TrechoRecuperado

# Estimativa pessimista para português: ~3 caracteres por token.
CARACTERES_POR_TOKEN = 3


def _motivo(resposta: httpx.Response) -> str:
    """Mensagem de erro que o Ollama devolveu ({"error": "..."}), para o diagnóstico."""
    try:
        motivo = str(resposta.json().get("error", ""))
    except (ValueError, AttributeError):
        motivo = resposta.text
    return (motivo.strip() or "sem detalhes")[:300]


class GeradorDeRespostaOllama:
    def __init__(
        self,
        base_url: str,
        modelo: str,
        *,
        max_tokens: int = 500,
        timeout_s: int = 300,
        num_ctx_max: int = 8192,
        cliente: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.modelo = modelo
        self.nome_modelo = f"ollama/{modelo}"
        self.max_tokens = max_tokens
        self.num_ctx_max = num_ctx_max
        self._cliente = cliente or httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5))
        self._timeout_s = timeout_s

    def _num_ctx(self, mensagens: list[dict[str, str]]) -> int:
        """Janela de contexto pedida. O Ollama usa um padrão pequeno e CORTA o prompt em silêncio
        se ele não couber, então a janela é dimensionada pelo tamanho real do prompt."""
        caracteres = sum(len(m["content"]) for m in mensagens)
        necessario = math.ceil(caracteres / CARACTERES_POR_TOKEN) + self.max_tokens + 256
        return min(max(4096, math.ceil(necessario / 1024) * 1024), self.num_ctx_max)

    def responder(self, pergunta: str, trechos: Sequence[TrechoRecuperado]) -> Resposta:
        mensagens = montar_mensagens(pergunta, trechos)
        corpo = {
            "model": self.modelo,
            "messages": mensagens,
            "stream": False,
            "options": {
                "temperature": 0,
                "num_ctx": self._num_ctx(mensagens),
                "num_predict": self.max_tokens,
            },
        }
        try:
            resposta = self._cliente.post(f"{self.base_url}/api/chat", json=corpo)
        except httpx.ConnectError as erro:
            raise ModeloIndisponivel(
                f"Não consegui falar com o Ollama em {self.base_url}. Ele está rodando?"
            ) from erro
        except httpx.TimeoutException as erro:
            raise ModeloIndisponivel(
                f"O Ollama não respondeu em {self._timeout_s}s (modelo grande demais para a CPU?)"
            ) from erro
        except httpx.HTTPError as erro:
            raise ModeloIndisponivel(
                f"Falha ao falar com o Ollama: {type(erro).__name__}"
            ) from erro

        if resposta.status_code == 404:
            raise ModeloIndisponivel(
                f"O Ollama não tem o modelo '{self.modelo}'. Baixe com: ollama pull {self.modelo}"
            )
        if resposta.status_code >= 400:
            raise ModeloIndisponivel(
                f"O Ollama respondeu com erro {resposta.status_code}: {_motivo(resposta)}"
            )

        dados = resposta.json()
        texto = limpar_saida(dados.get("message", {}).get("content", ""))
        if not texto:
            raise ModeloIndisponivel("O Ollama devolveu uma resposta vazia")
        recusou = e_recusa(texto)
        return Resposta(
            texto=texto,
            fontes=() if recusou else extrair_citacoes(texto, len(trechos)),
            recusou=recusou,
            tokens_entrada=int(dados.get("prompt_eval_count", 0)),
            tokens_saida=int(dados.get("eval_count", 0)),
            custo=0.0,
        )
