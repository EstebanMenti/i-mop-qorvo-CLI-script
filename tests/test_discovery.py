"""Tests del descubrimiento de placas (fase F1)."""

from types import SimpleNamespace

import pytest

import dwm3001c_cli.transport.discovery as discovery_module
from dwm3001c_cli.transport.discovery import BoardPort, find_boards


def _port_info(
    device: str,
    vid: int | None,
    description: str = "desc",
    serial_number: str | None = "SN123",
) -> SimpleNamespace:
    return SimpleNamespace(
        device=device, vid=vid, description=description, serial_number=serial_number
    )


def test_filters_by_known_vids(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ports = [
        _port_info("COM9", vid=0x1366, description="JLink CDC UART Port"),
        _port_info("COM3", vid=0x2341),  # Arduino: no es una placa DWM
        _port_info("COM7", vid=0x1915, description="nRF USB"),
        _port_info("COM4", vid=None),  # puerto sin metadata USB
    ]
    monkeypatch.setattr(discovery_module.list_ports, "comports", lambda: fake_ports)

    boards = find_boards()

    assert [b.port for b in boards] == ["COM7", "COM9"]  # ordenado por puerto
    assert boards[0].interface_hint == "nrf-usb"
    assert boards[1].interface_hint == "jlink-uart"
    assert boards[1].description == "JLink CDC UART Port"


def test_no_boards_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery_module.list_ports, "comports", lambda: [])

    assert find_boards() == []


@pytest.mark.hardware
def test_find_boards_on_real_system() -> None:
    """Con placas DWM3001CDK conectadas: deben aparecer como candidatos.

    Correr con: ``pytest -m hardware -s tests/test_discovery.py``
    """
    boards = find_boards()

    for board in boards:
        print(board)
    assert isinstance(boards, list)
    assert all(isinstance(board, BoardPort) for board in boards)
