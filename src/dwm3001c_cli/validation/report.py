"""Reportes de la suite de validación: consola (Rich), JSON y Markdown (plan §5.3)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from dwm3001c_cli.core.models import DeviceInfo, ValidationResult

_STATUS_STYLES = {"PASS": "green", "FAIL": "red", "SKIP": "yellow"}


def status_of(result: ValidationResult) -> str:
    if result.detail.startswith("SKIP"):
        return "SKIP"
    return "PASS" if result.passed else "FAIL"


def summarize(results: Sequence[ValidationResult]) -> dict[str, int]:
    counts = {"total": len(results), "pass": 0, "fail": 0, "skip": 0}
    for result in results:
        counts[status_of(result).lower()] += 1
    return counts


def build_table(results: Sequence[ValidationResult]) -> Table:
    table = Table(title="Validación de comandos CLI — DWM3001CDK")
    table.add_column("Check")
    table.add_column("Estado")
    table.add_column("Duración", justify="right")
    table.add_column("Detalle", overflow="fold")
    for result in results:
        status = status_of(result)
        table.add_row(
            result.command,
            f"[{_STATUS_STYLES[status]}]{status}[/]",
            f"{result.duration_s:.1f} s",
            result.detail,
        )
    return table


def render_console(results: Sequence[ValidationResult], console: Console | None = None) -> None:
    (console or Console()).print(build_table(results))


def write_reports(
    results: Sequence[ValidationResult],
    *,
    report_dir: Path,
    port: str,
    device: DeviceInfo | None = None,
) -> tuple[Path, Path]:
    """Escribe ``validacion-<puerto>-<fecha>.json`` y ``.md``; devuelve ambas rutas."""
    report_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    base = f"validacion-{port}-{now:%Y%m%d-%H%M%S}"
    json_path = report_dir / f"{base}.json"
    md_path = report_dir / f"{base}.md"

    payload = {
        "puerto": port,
        "fecha": now.isoformat(timespec="seconds"),
        "dispositivo": asdict(device) if device is not None else None,
        "resumen": summarize(results),
        "resultados": [asdict(result) for result in results],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path.write_text(_render_markdown(results, port=port, now=now, device=device), "utf-8")
    return json_path, md_path


def _render_markdown(
    results: Sequence[ValidationResult],
    *,
    port: str,
    now: datetime,
    device: DeviceInfo | None,
) -> str:
    counts = summarize(results)
    lines = [
        "# Reporte de validación de comandos CLI — DWM3001CDK",
        "",
        f"> **Placa:** {port} · **Fecha:** {now:%Y-%m-%d %H:%M:%S}",
    ]
    if device is not None:
        lines.append(
            f"> **Firmware:** {device.version} ({device.build}) · **Dispositivo:** {device.device}"
        )
    lines += [
        "",
        f"**Resultado:** {counts['pass']} PASS · {counts['fail']} FAIL · "
        f"{counts['skip']} SKIP (total {counts['total']})",
        "",
        "| Check | Estado | Duración | Detalle |",
        "|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            f"| {result.command} | {status_of(result)} "
            f"| {result.duration_s:.1f} s | {result.detail} |"
        )

    failures = [result for result in results if status_of(result) == "FAIL"]
    if failures:
        lines += ["", "## Respuestas crudas de los checks fallidos", ""]
        for result in failures:
            lines += [f"### {result.command} (`{result.sent}`)", "", "```text"]
            lines += list(result.response_lines) or ["(sin respuesta)"]
            lines += ["```", ""]
    lines.append("")
    return "\n".join(lines)
