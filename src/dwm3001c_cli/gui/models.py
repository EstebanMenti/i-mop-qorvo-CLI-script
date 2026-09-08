"""Modelos Qt para mostrar resultados en vivo en las vistas de la GUI."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QColor

from dwm3001c_cli.core.models import ValidationResult
from dwm3001c_cli.validation.report import status_of

_HEADERS = ("Check", "Dispositivo", "Estado", "Duración", "Detalle")

_COLOR_PASS = QColor("#1a7f37")  # verde
_COLOR_FAIL = QColor("#c62828")  # rojo
_COLOR_SKIP = QColor("#6b7280")  # gris


class ValidationResultsModel(QAbstractTableModel):
    """Tabla de resultados de :func:`~dwm3001c_cli.validation.runner.run_validation`.

    Se llena fila por fila a medida que llegan los ``ValidationResult`` (vía
    ``on_result``, ver ``gui/workers.py``), no de una sola vez al final.
    """

    def __init__(self) -> None:
        super().__init__()
        self._results: list[ValidationResult] = []
        self._primary_device: str = ""
        self._second_device: str | None = None

    def start_run(self, primary_device: str, second_device: str | None = None) -> None:
        """Registra qué placas participan de la corrida (columna Dispositivo).

        Args:
            primary_device: nombre de la placa principal (la que se valida),
                tal como ``Transport.name`` (p. ej. ``COM7`` o
                ``BLE-CCEBFE5BC5E9``).
            second_device: nombre de la segunda placa; si está presente,
                habilita el check C4, que usa ambas.
        """
        self._primary_device = primary_device
        self._second_device = second_device

    def add_result(self, result: ValidationResult) -> None:
        row = len(self._results)
        self.beginInsertRows(QModelIndex(), row, row)
        self._results.append(result)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._results = []
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._results)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return _HEADERS[section]

    def _device_of(self, result: ValidationResult) -> str:
        """Dispositivo(s) involucrados en el check de esta fila.

        Todos los checks corren sobre la placa principal; solo C4 (sesión TWR)
        usa además la segunda placa.
        """
        if self._second_device is not None and result.command.startswith("C4"):
            return f"{self._primary_device} + {self._second_device}"
        return self._primary_device

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        result = self._results[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.ForegroundRole and column == 2:
            status = status_of(result)
            if status == "PASS":
                return _COLOR_PASS
            if status == "FAIL":
                return _COLOR_FAIL
            return _COLOR_SKIP
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == 0:
            return result.command
        if column == 1:
            return self._device_of(result)
        if column == 2:
            return status_of(result)
        if column == 3:
            return f"{result.duration_s:.1f} s"
        if column == 4:
            return result.detail
        return None
