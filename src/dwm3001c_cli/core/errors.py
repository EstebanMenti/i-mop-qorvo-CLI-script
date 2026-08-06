"""Jerarquía de excepciones del proyecto.

Todas las excepciones propias derivan de :class:`Dwm3001cError`, de modo que la
capa de aplicación pueda capturarlas en un único punto y traducirlas a mensajes
claros para el usuario (ver plan de implementación, fase F0).
"""

from __future__ import annotations


class Dwm3001cError(Exception):
    """Base de todas las excepciones del paquete ``dwm3001c_cli``."""


class TransportError(Dwm3001cError):
    """Falla de la capa de transporte serie.

    Ejemplos: el puerto no se pudo abrir, la placa se desconectó durante una
    operación, o el sistema operativo rechazó la escritura.
    """


class CommandTimeoutError(Dwm3001cError):
    """El firmware no respondió ninguna línea dentro del tiempo esperado."""

    def __init__(self, port: str, command: str, timeout_s: float) -> None:
        super().__init__(
            f"Sin respuesta del puerto {port} al comando {command!r} "
            f"tras {timeout_s:.1f} s de espera"
        )
        self.port = port
        self.command = command
        self.timeout_s = timeout_s


class CommandRejectedError(Dwm3001cError):
    """El firmware respondió con error, o sin ``ok`` cuando se esperaba ``ok``."""


class UnexpectedModeError(Dwm3001cError):
    """El dispositivo no está en el modo requerido para la operación.

    Caso típico: un comando de servicio/IDLE exige modo NONE y el dispositivo
    no llegó a NONE tras enviar ``STOP`` (guía, §1.3).
    """


class DeviceDiscoveryError(Dwm3001cError):
    """No se encontraron placas DWM3001CDK o el puerto indicado no existe."""


class CalibrationError(Dwm3001cError):
    """El bucle de calibración no converge o detectó una condición insegura."""
