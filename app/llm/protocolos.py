"""As duas interfaces do RAG. Cada uma tem implementação local, por API e simulada."""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.llm.tipos import Resposta, TrechoRecuperado


@runtime_checkable
class GeradorDeEmbeddings(Protocol):
    """Transforma texto em vetores.

    Há dois métodos porque alguns modelos tratam diferente o texto que se guarda e a pergunta
    (os da família E5, por exemplo, pedem prefixos distintos). Quem chama não precisa saber.
    """

    nome_modelo: str
    dimensao: int

    def gerar_trechos(self, textos: Sequence[str]) -> list[list[float]]: ...

    def gerar_consulta(self, texto: str) -> list[float]: ...


@runtime_checkable
class GeradorDeResposta(Protocol):
    """Escreve a resposta a partir da pergunta e dos trechos recuperados.

    Deve citar só o que está nos trechos e recusar (Resposta.recusou) quando não houver evidência.
    """

    nome_modelo: str

    def responder(self, pergunta: str, trechos: Sequence[TrechoRecuperado]) -> Resposta: ...
