"""Logs em JSON, uma linha por evento.

Registre identificadores, tamanhos e tempos; nunca o texto completo de documentos.
"""

import json
import logging
import sys
from collections import deque
from datetime import UTC, datetime

_CAMPOS_PADRAO = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        dados: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "nivel": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for chave, valor in record.__dict__.items():
            if chave not in _CAMPOS_PADRAO:
                dados[chave] = valor
        if record.exc_info:
            dados["erro"] = self.formatException(record.exc_info)
        return json.dumps(dados, ensure_ascii=False, default=str)


class Memoria(logging.Handler):
    """Guarda os últimos eventos na memória do processo, para a tela de logs.

    Vale só para este processo: com vários workers, cada um tem o seu. O que vai para o arquivo ou
    para o coletor de logs continua sendo a saída padrão."""

    def __init__(self, capacidade: int = 1000) -> None:
        super().__init__()
        self.eventos: deque[dict[str, object]] = deque(maxlen=capacidade)
        self._formato = JsonFormatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.eventos.append(json.loads(self._formato.format(record)))
        except Exception:  # um log nunca pode derrubar quem o escreveu
            self.handleError(record)


MEMORIA = Memoria()


def configure_logging(nivel: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    raiz = logging.getLogger()
    raiz.handlers = [handler, MEMORIA]
    raiz.setLevel(nivel)

    # A própria aplicação registra cada requisição; o log de acesso do uvicorn seria duplicado.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for nome in ("uvicorn", "uvicorn.error"):
        logging.getLogger(nome).handlers = []
