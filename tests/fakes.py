"""Transporte simulado para tests sin hardware.

``FakeTransport`` implementa el protocolo ``Transport`` reproduciendo respuestas
del firmware a partir de un guion (comando → líneas de respuesta) y de una cola
de notificaciones espontáneas (para simular ``SESSION_INFO_NTF`` durante una
sesión de ranging).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable


class FakeTransport:
    """Simulación de una placa DWM3001CDK detrás de un puerto serie.

    Args:
        script: mapa de línea de comando exacta → líneas de respuesta. Si el
            comando enviado no figura, se busca por su primera palabra (útil
            para comandos con parámetros variables). Sin coincidencia, no se
            encola respuesta (simula silencio → timeout).
        notifications: líneas espontáneas que se entregan de a una cuando no
            hay respuestas pendientes (simula notificaciones de ranging).
    """

    def __init__(
        self,
        script: dict[str, list[str]] | None = None,
        notifications: Iterable[str] = (),
    ) -> None:
        self.script = dict(script or {})
        self.notifications: deque[str] = deque(notifications)
        self.sent: list[str] = []
        self.opened = False
        self._pending: deque[str] = deque()

    @property
    def name(self) -> str:
        return "FAKE"

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def write_line(self, line: str) -> None:
        self.sent.append(line)
        response = self.script.get(line)
        if response is None:
            first_word = line.split(maxsplit=1)[0] if line.strip() else line
            response = self.script.get(first_word)
        if response is not None:
            self._pending.extend(response)

    def read_line(self, timeout_s: float) -> str | None:
        if self._pending:
            return self._pending.popleft()
        if self.notifications:
            return self.notifications.popleft()
        return None

    def push_lines(self, lines: Iterable[str]) -> None:
        """Encola líneas arbitrarias como si llegaran de la placa."""
        self._pending.extend(lines)
