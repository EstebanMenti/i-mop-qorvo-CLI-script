"""Tests de la jerarquía de excepciones (fase F0)."""

import pytest

from dwm3001c_cli.core.errors import (
    CalibrationError,
    CommandRejectedError,
    CommandTimeoutError,
    DeviceDiscoveryError,
    Dwm3001cError,
    TransportError,
    UnexpectedModeError,
)

ALL_ERRORS = [
    TransportError,
    CommandTimeoutError,
    CommandRejectedError,
    UnexpectedModeError,
    DeviceDiscoveryError,
    CalibrationError,
]


@pytest.mark.parametrize("error_class", ALL_ERRORS)
def test_all_errors_derive_from_base(error_class: type[Exception]) -> None:
    assert issubclass(error_class, Dwm3001cError)
    assert issubclass(error_class, Exception)


def test_command_timeout_message_includes_context() -> None:
    error = CommandTimeoutError("COM7", "STAT", 2.0)

    message = str(error)
    assert "COM7" in message
    assert "STAT" in message
    assert "2.0" in message
    assert error.port == "COM7"
    assert error.command == "STAT"
    assert error.timeout_s == 2.0


def test_base_error_can_be_caught_generically() -> None:
    with pytest.raises(Dwm3001cError):
        raise TransportError("no se pudo abrir COM7")
