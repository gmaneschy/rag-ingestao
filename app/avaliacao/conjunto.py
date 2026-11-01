"""O conjunto de perguntas de referência: o que se pergunta e onde está a resposta.

O trecho correto não é identificado por `id`: ele muda a cada reprocessamento e a cada tamanho de
trecho testado. A referência é o lugar no documento: parte do nome do arquivo, faixa de páginas
e termos que o trecho precisa conter. Assim o mesmo conjunto vale para qualquer configuração.
"""

import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class Suporte(BaseModel):
    """Onde a resposta está. Um trecho acerta se casa com TODAS as condições informadas."""

    model_config = ConfigDict(extra="forbid")

    documento: Annotated[str, StringConstraints(min_length=1)]  # parte do nome do arquivo
    # Faixa [de, ate] (inclusive) das páginas do PDF; uma página só: [183, 183]. Páginas soltas
    # e distantes (44 e 176) são dois suportes, cada um com a sua faixa.
    paginas: tuple[int, int] | None = None
    contem: list[str] = Field(default_factory=list)  # termos que o texto precisa ter (TODOS)
    # Alternativas: basta o texto ter UM destes ("piano", "violoncelo", "canto"...).
    contem_algum: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validar(self) -> "Suporte":
        if self.paginas and self.paginas[0] > self.paginas[1]:
            raise ValueError("paginas deve ser [de, ate] com de <= ate")
        if not (self.paginas or self.contem or self.contem_algum):
            raise ValueError(
                "informe paginas, contem ou contem_algum: só o documento é largo demais"
            )
        return self


class Caso(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    pergunta: Annotated[str, StringConstraints(min_length=3)]
    colecoes: list[str] | None = None
    # False = a resposta NÃO está no corpus: o certo é recusar.
    responde: bool = True
    resposta_esperada: str | None = None  # para conferência humana, não entra nas métricas
    # Qualquer um dos suportes serve (a mesma informação pode estar em mais de um lugar).
    suporte: list[Suporte] = Field(default_factory=list)
    observacao: str | None = None

    @model_validator(mode="after")
    def validar(self) -> "Caso":
        if self.responde and not self.suporte:
            raise ValueError(f"caso {self.id}: pergunta com resposta precisa de 'suporte'")
        if not self.responde and self.suporte:
            raise ValueError(f"caso {self.id}: pergunta sem resposta não tem 'suporte'")
        return self


class Conjunto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    casos: list[Caso]

    @model_validator(mode="after")
    def validar(self) -> "Conjunto":
        ids = [c.id for c in self.casos]
        repetidos = {i for i in ids if ids.count(i) > 1}
        if repetidos:
            raise ValueError(f"ids repetidos: {sorted(repetidos)}")
        if not self.casos:
            raise ValueError("o conjunto está vazio")
        return self


def carregar(caminho: Path) -> Conjunto:
    return Conjunto.model_validate(json.loads(caminho.read_text(encoding="utf-8")))
