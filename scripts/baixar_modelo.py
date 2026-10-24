"""Baixa o modelo de embeddings antes do primeiro uso, com novas tentativas.

O Hugging Face limita requisições anônimas (erro 429). Rodar isto uma vez, fora da ingestão,
evita que o primeiro documento falhe por isso. Com uma conta gratuita, defina HF_TOKEN para um
limite maior:

    $env:HF_TOKEN = "hf_..."            # PowerShell, só nesta janela
    uv run python scripts/baixar_modelo.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # permite `import app`

from app.ingestao.config import get_ingestao_settings  # noqa: E402
from app.llm.config import get_llm_settings  # noqa: E402
from app.llm.local import EmbeddingsLocal  # noqa: E402
from app.llm.tipos import ModeloIndisponivel  # noqa: E402

TENTATIVAS = 6


def main() -> int:
    llm = get_llm_settings()
    modelo = EmbeddingsLocal(
        llm.embeddings_model, llm.embeddings_dim, get_ingestao_settings().modelos_dir
    )
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            vetor = modelo.gerar_consulta("teste de carregamento do modelo")
        except ModeloIndisponivel as erro:
            espera = 30 * tentativa
            print(f"tentativa {tentativa}/{TENTATIVAS} falhou ({erro}); aguardando {espera}s")
            if tentativa < TENTATIVAS:
                time.sleep(espera)
            continue
        print(f"modelo pronto: {llm.embeddings_model}, {len(vetor)} dimensões")
        return 0
    print("Não consegui baixar o modelo. Tente mais tarde ou defina HF_TOKEN.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
