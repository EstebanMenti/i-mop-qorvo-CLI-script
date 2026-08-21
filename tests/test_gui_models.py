"""Tests de ValidationResultsModel (fase F9), sin hardware ni ventana visible."""

from __future__ import annotations

from dwm3001c_cli.core.models import ValidationResult
from dwm3001c_cli.gui.models import ValidationResultsModel


def _result(command: str = "A1 HELP", passed: bool = True, detail: str = "ok") -> ValidationResult:
    return ValidationResult(
        command=command,
        sent="HELP",
        passed=passed,
        detail=detail,
        response_lines=("ok",),
        duration_s=0.1,
    )


def test_starts_empty() -> None:
    model = ValidationResultsModel()

    assert model.rowCount() == 0
    assert model.columnCount() == 4


def test_add_result_grows_row_count() -> None:
    model = ValidationResultsModel()

    model.add_result(_result())

    assert model.rowCount() == 1


def test_data_reports_status_and_detail() -> None:
    model = ValidationResultsModel()
    model.add_result(_result(command="A1 HELP", passed=True, detail="lista de comandos"))

    assert model.data(model.index(0, 0)) == "A1 HELP"
    assert model.data(model.index(0, 1)) == "PASS"
    assert model.data(model.index(0, 3)) == "lista de comandos"


def test_data_reports_fail_status() -> None:
    model = ValidationResultsModel()
    model.add_result(_result(passed=False, detail="mal"))

    assert model.data(model.index(0, 1)) == "FAIL"


def test_data_reports_skip_status() -> None:
    model = ValidationResultsModel()
    model.add_result(_result(detail="SKIP: requiere una segunda placa conectada"))

    assert model.data(model.index(0, 1)) == "SKIP"


def test_clear_resets_rows() -> None:
    model = ValidationResultsModel()
    model.add_result(_result())

    model.clear()

    assert model.rowCount() == 0


def test_header_data() -> None:
    from PySide6.QtCore import Qt

    model = ValidationResultsModel()

    assert model.headerData(0, Qt.Orientation.Horizontal) == "Check"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Estado"
