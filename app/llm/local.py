"""Embeddings locais, em CPU, com fastembed (ONNX, sem PyTorch).

O modelo é baixado na primeira vez e guardado em `modelos_dir`; depois funciona offline.
O fastembed é importado só aqui, dentro do método, para o resto da aplicação e os testes não
pagarem o custo de carregá-lo.
"""

import threading
from collections.abc import Sequence
from pathlib import Path

from app.llm.perfis import perfil_de
from app.llm.tipos import ModeloIndisponivel


class EmbeddingsLocal:
    def __init__(self, nome_modelo: str, dimensao: int, cache_dir: Path | None = None) -> None:
        self.nome_modelo = nome_modelo
        self.dimensao = dimensao
        self._cache_dir = cache_dir
        self._modelo = None
        self._trava = threading.Lock()

    def _carregar(self):
        with self._trava:
            if self._modelo is None:
                from fastembed import TextEmbedding

                try:
                    self._modelo = TextEmbedding(
                        model_name=self.nome_modelo,
                        cache_dir=str(self._cache_dir) if self._cache_dir else None,
                    )
                except Exception as erro:  # o fastembed levanta ValueError, OSError, HTTPError...
                    raise ModeloIndisponivel(
                        f"Não consegui carregar o modelo {self.nome_modelo}: {type(erro).__name__}"
                    ) from erro
        return self._modelo

    def _gerar(self, textos: Sequence[str], *, consulta: bool) -> list[list[float]]:
        perfil = perfil_de(self.nome_modelo)
        prefixo = perfil.prefixo_consulta if consulta else perfil.prefixo_trecho
        entrada = [prefixo + t for t in textos] if prefixo else list(textos)
        modelo = self._carregar()
        if perfil.tarefas:  # o modelo escolhe a tarefa (consulta ou trecho) por conta própria
            metodo = modelo.query_embed if consulta else modelo.passage_embed
            vetores = [v.tolist() for v in metodo(entrada, batch_size=32)]
        else:
            vetores = [v.tolist() for v in modelo.embed(entrada, batch_size=32)]
        for vetor in vetores:
            if len(vetor) != self.dimensao:
                raise ValueError(
                    f"O modelo {self.nome_modelo} devolveu {len(vetor)} dimensões; "
                    f"o esperado era {self.dimensao} (EMBEDDINGS_DIM)"
                )
        return vetores

    # Os prefixos de cada modelo vêm de `perfis`: a pergunta e o trecho guardado são tratados
    # de forma diferente nos modelos feitos para busca (família E5, Gemma, Qwen).
    def gerar_trechos(self, textos: Sequence[str]) -> list[list[float]]:
        return self._gerar(textos, consulta=False) if textos else []

    def gerar_consulta(self, texto: str) -> list[float]:
        return self._gerar([texto], consulta=True)[0]
