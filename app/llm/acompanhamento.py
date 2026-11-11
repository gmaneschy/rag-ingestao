"""Acompanhar e cancelar uma pergunta em andamento, de outra thread.

A pergunta roda em segundo plano (a tela do chat só a consulta de tempos em tempos). Quem a executa
avisa a etapa e o texto parcial por aqui e confere, entre uma etapa e outra e a cada pedaço do
texto, se pediram para parar."""

import threading


class Cancelado(Exception):
    """A pergunta foi interrompida por quem a fez."""


class Acompanhamento:
    def __init__(self) -> None:
        self._parar = threading.Event()
        self._trava = threading.Lock()
        self._etapa = ""
        self._texto = ""

    # --- quem pede para parar
    def cancelar(self) -> None:
        self._parar.set()

    @property
    def cancelado(self) -> bool:
        return self._parar.is_set()

    # --- quem executa
    def conferir(self) -> None:
        if self._parar.is_set():
            raise Cancelado

    def etapa(self, nome: str) -> None:
        with self._trava:
            self._etapa = nome

    def texto(self, parcial: str) -> None:
        with self._trava:
            self._texto = parcial

    # --- quem mostra
    def situacao(self) -> tuple[str, str]:
        """(etapa, texto parcial) neste instante."""
        with self._trava:
            return self._etapa, self._texto
