"""Modelos Qt para mostrar resultados en vivo en las vistas de la GUI."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt

from dwm3001c_cli.core.models import ValidationResult
from dwm3001c_cli.validation.report import status_of

_HEADERS = ("Check", "Estado", "Duración", "Detalle")


class ValidationResultsModel(QAbstractTableModel):
    """Tabla de resultados de :func:`~dwm3001c_cli.validation.runner.run_validation`.

    Se llena fila por fila a medida que llegan los ``ValidationResult`` (vía
    ``on_result``, ver ``gui/workers.py``), no de una sola vez al final.
    """

    def __init__(self) -> None:
        super().__init__()
        self._results: list[ValidationResult] = []

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

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        result = self._results[index.row()]
        column = index.column()
        if column == 0:
            return result.command
        if column == 1:
            return status_of(result)
        if column == 2:
            return f"{result.duration_s:.1f} s"
        if column == 3:
            return result.detail
        return None
