"""Logs em JSON, uma linha por evento.

Registre identificadores, tamanhos e tempos; nunca o texto completo de documentos.
"""

import json
import logging
import sys
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


def configure_logging(nivel: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    raiz = logging.getLogger()
    raiz.handlers = [handler]
    raiz.setLevel(nivel)

    # A própria aplicação registra cada requisição; o log de acesso do uvicorn seria duplicado.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for nome in ("uvicorn", "uvicorn.error"):
        logging.getLogger(nome).handlers = []
