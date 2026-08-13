"""Tests de ble_discovery (rama hardware/ble-bridge-nrf52840), sin hardware.

Prueban :func:`find_ble_boards` (el envoltorio síncrono, ``asyncio.run`` por
dentro) en vez de la corutina ``scan_ble_boards`` directamente, para no sumar
``pytest-asyncio`` como dependencia nueva solo para estos tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dwm3001c_cli.transport import ble_discovery
from dwm3001c_cli.transport.ble_link import NUS_SERVICE_UUID


@dataclass
class _FakeDevice:
    address: str
    name: str | None


@dataclass
class _FakeAdvertisement:
    local_name: str | None
    service_uuids: list[str]
    rssi: int | None


def _fake_discover(
    found: dict[str, tuple[_FakeDevice, _FakeAdvertisement]],
):
    async def discover(timeout: float = 5.0, *, return_adv: bool = False, **kwargs: object):
        assert return_adv is True
        return found

    return discover


def test_matches_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    found = {
        "FD:7A:90:57:CC:9F": (
            _FakeDevice("FD:7A:90:57:CC:9F", "UWB Node"),
            _FakeAdvertisement(local_name="UWB Node", service_uuids=[], rssi=-40),
        ),
        "11:22:33:44:55:66": (
            _FakeDevice("11:22:33:44:55:66", "Otro dispositivo"),
            _FakeAdvertisement(local_name="Otro dispositivo", service_uuids=[], rssi=-70),
        ),
    }
    monkeypatch.setattr(ble_discovery.BleakScanner, "discover", staticmethod(_fake_discover(found)))

    boards = ble_discovery.find_ble_boards()

    assert [b.address for b in boards] == ["FD:7A:90:57:CC:9F"]
    assert boards[0].rssi == -40


def test_matches_by_nus_service_uuid_when_name_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    found = {
        "FD:7A:90:57:CC:9F": (
            _FakeDevice("FD:7A:90:57:CC:9F", None),
            _FakeAdvertisement(local_name=None, service_uuids=[NUS_SERVICE_UUID.upper()], rssi=-50),
        ),
    }
    monkeypatch.setattr(ble_discovery.BleakScanner, "discover", staticmethod(_fake_discover(found)))

    boards = ble_discovery.find_ble_boards(name_filter="")

    assert [b.address for b in boards] == ["FD:7A:90:57:CC:9F"]


def test_no_match_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    found = {
        "11:22:33:44:55:66": (
            _FakeDevice("11:22:33:44:55:66", "Otro dispositivo"),
            _FakeAdvertisement(local_name="Otro dispositivo", service_uuids=[], rssi=-70),
        ),
    }
    monkeypatch.setattr(ble_discovery.BleakScanner, "discover", staticmethod(_fake_discover(found)))

    boards = ble_discovery.find_ble_boards()

    assert boards == []
