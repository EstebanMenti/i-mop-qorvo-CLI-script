"""Bucle de calibración automática del retardo de antena (plan §6.2).

Calibra la clave ``ant<x>.ch<y>.ant_delay`` del **dispositivo bajo prueba**
(rol RESPF) contra un dispositivo de **referencia** (rol INITF) colocado a una
distancia real conocida. El método sigue el procedimiento iterativo oficial
(guía §4.1) con una mejora: la sensibilidad (cm por unidad de retardo) no se
asume, **se mide** con un escalón conocido en la primera corrección.

Regla de signo (guía §4.2): aumentar el retardo de antena **reduce** la
distancia reportada.

Seguridad:
- El valor original se respalda en un archivo antes de escribir nada.
- Toda condición de error **restaura el valor original** en la placa.
- La corrección total está acotada (``max_total_correction_units``).
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from dwm3001c_cli.calibration.sampler import SessionParams, collect_samples
from dwm3001c_cli.core.client import DwmCliClient
from dwm3001c_cli.core.errors import CalibrationError
from dwm3001c_cli.core.models import RangingStats

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutocalConfig:
    """Parámetros del bucle de calibración (defaults del plan §6.2)."""

    n_samples: int = 100
    tolerance_cm: float = 2.0
    max_iterations: int = 6
    probe_step_units: int = 20
    max_total_correction_units: int = 1500
    channel: int = 9
    antenna_path: int = 0
    do_save: bool = True

    @property
    def key(self) -> str:
        """Clave de calibración de distancia (guía §3.2)."""
        return f"ant{self.antenna_path}.ch{self.channel}.ant_delay"


@dataclass(frozen=True)
class CalibrationIteration:
    """Registro de una medición del bucle, para trazabilidad."""

    index: int
    delay: int
    mean_cm: float
    std_cm: float
    n_success: int
    error_cm: float
    correction_units: int | None


@dataclass(frozen=True)
class CalibrationReport:
    """Resultado completo de una corrida de calibración."""

    device_port: str
    reference_port: str
    key: str
    real_distance_cm: float
    initial_delay: int
    final_delay: int
    sensitivity_cm_per_unit: float | None
    iterations: tuple[CalibrationIteration, ...]
    converged: bool
    saved: bool


def autocalibrate(
    device: DwmCliClient,
    reference: DwmCliClient,
    *,
    real_distance_m: float,
    config: AutocalConfig | None = None,
    report_dir: Path = Path("reports"),
    on_iteration: Callable[[CalibrationIteration], None] | None = None,
) -> CalibrationReport:
    """Calibra el retardo de antena de ``device`` contra ``reference``.

    ``device`` actúa de RESPF (es el que se modifica); ``reference`` de INITF
    y **no se toca**. Las mediciones se leen del initiator.

    Args:
        on_iteration: callback opcional invocado con cada :class:`CalibrationIteration`
            apenas se completa su medición (antes de decidir la próxima corrección);
            permite mostrar progreso en vivo (p. ej. la CLI, plan §7) sin acoplar
            este módulo a ninguna capa de presentación.

    Raises:
        CalibrationError: enlace pobre, sensibilidad no medible, salvaguarda
            violada o no convergencia. En todos los casos el retardo original
            queda restaurado en la placa (y respaldado en ``report_dir``).
    """
    cfg = config or AutocalConfig()
    if real_distance_m <= 0:
        raise ValueError(f"Distancia real inválida: {real_distance_m} m")
    real_cm = real_distance_m * 100.0
    key = cfg.key
    params = SessionParams(chan=cfg.channel)

    device.ensure_mode_none()
    reference.ensure_mode_none()
    initial_delay = device.calkey_read(key).value
    backup_path = _write_backup(report_dir, device.name, key, initial_delay)
    logger.info(
        "Calibrando %s de %s (valor inicial %d); backup en %s",
        key,
        device.name,
        initial_delay,
        backup_path,
    )

    iterations: list[CalibrationIteration] = []
    sensitivity: float | None = None
    current_delay = initial_delay

    def measure(correction: int | None) -> tuple[float, RangingStats]:
        stats = collect_samples(reference, device, n_samples=cfg.n_samples, session_params=params)
        error = stats.mean_cm - real_cm
        iterations.append(
            CalibrationIteration(
                index=len(iterations),
                delay=current_delay,
                mean_cm=stats.mean_cm,
                std_cm=stats.std_cm,
                n_success=stats.n_success,
                error_cm=error,
                correction_units=correction,
            )
        )
        logger.info(
            "Iteración %d: delay=%d, media=%.1f cm, error=%+.1f cm",
            len(iterations) - 1,
            current_delay,
            stats.mean_cm,
            error,
        )
        if on_iteration is not None:
            on_iteration(iterations[-1])
        return error, stats

    try:
        error, stats_initial = measure(None)

        if abs(error) > cfg.tolerance_cm:
            # --- Sondeo de sensibilidad [Fuera del manual, guía §4.2 paso 5] ---
            current_delay = initial_delay + cfg.probe_step_units
            device.calkey_write(key, current_delay)
            error, stats_probe = measure(cfg.probe_step_units)
            change_cm = stats_initial.mean_cm - stats_probe.mean_cm
            # Ruido de la DIFERENCIA DE MEDIAS: se propaga el error estándar de
            # cada media (std/sqrt(n)), no el desvío de las muestras — con
            # multipath el desvío individual puede superar al escalón sin que
            # la media deje de ser confiable (verificado en banco real).
            sem_initial = stats_initial.std_cm / math.sqrt(max(stats_initial.n_success, 1))
            sem_probe = stats_probe.std_cm / math.sqrt(max(stats_probe.n_success, 1))
            noise_cm = 2.0 * math.hypot(sem_initial, sem_probe)
            if change_cm <= 0 or change_cm < noise_cm:
                raise CalibrationError(
                    f"El escalón de +{cfg.probe_step_units} unidades no produjo un "
                    f"cambio medible (delta={change_cm:.1f} cm, ruido 2*std={noise_cm:.1f} cm). "
                    "Revisar montaje o aumentar probe_step_units."
                )
            sensitivity = change_cm / cfg.probe_step_units
            logger.info("Sensibilidad medida: %.3f cm/unidad", sensitivity)

            # --- Bucle de corrección ---
            corrections_done = 0
            while abs(error) > cfg.tolerance_cm and corrections_done < cfg.max_iterations:
                correction = round(error / sensitivity)
                if correction == 0:
                    correction = 1 if error > 0 else -1
                delay_next = current_delay + correction
                drift = abs(delay_next - initial_delay)
                if delay_next <= 0 or drift > cfg.max_total_correction_units:
                    raise CalibrationError(
                        f"Salvaguarda: el nuevo delay {delay_next} excede la corrección "
                        f"máxima permitida (±{cfg.max_total_correction_units} unidades "
                        f"desde {initial_delay}). Verificar la distancia real ingresada."
                    )
                device.calkey_write(key, delay_next)
                current_delay = delay_next
                error, _ = measure(correction)
                corrections_done += 1

            if abs(error) > cfg.tolerance_cm:
                raise CalibrationError(
                    f"Sin convergencia tras {cfg.max_iterations} correcciones "
                    f"(error final {error:+.1f} cm, tolerancia ±{cfg.tolerance_cm} cm)."
                )
    except Exception:
        _restore(device, key, initial_delay, current_delay, backup_path)
        raise

    saved = False
    if cfg.do_save:
        device.ensure_mode_none()
        device.save()
        saved = True
    final_delay = device.calkey_read(key).value

    report = CalibrationReport(
        device_port=device.name,
        reference_port=reference.name,
        key=key,
        real_distance_cm=real_cm,
        initial_delay=initial_delay,
        final_delay=final_delay,
        sensitivity_cm_per_unit=sensitivity,
        iterations=tuple(iterations),
        converged=True,
        saved=saved,
    )
    write_calibration_report(report, report_dir)
    return report


def _write_backup(report_dir: Path, port: str, key: str, value: int) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"backup-calkey-{port}-{datetime.now():%Y%m%d-%H%M%S}.json"
    payload = {
        "puerto": port,
        "clave": key,
        "valor": value,
        "fecha": datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _restore(
    device: DwmCliClient, key: str, initial_delay: int, current_delay: int, backup_path: Path
) -> None:
    """Devuelve la clave a su valor original tras un fallo del bucle."""
    if current_delay == initial_delay:
        return
    try:
        device.ensure_mode_none()
        device.calkey_write(key, initial_delay)
        logger.info("Restaurado %s=%d en %s", key, initial_delay, device.name)
    except Exception:
        logger.exception(
            "No se pudo restaurar %s=%d en %s — el valor original está en %s",
            key,
            initial_delay,
            device.name,
            backup_path,
        )


def write_calibration_report(report: CalibrationReport, report_dir: Path) -> tuple[Path, Path]:
    """Escribe ``calibracion-<placa>-<fecha>.json`` y ``.md``; devuelve las rutas."""
    report_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    base = f"calibracion-{report.device_port}-{now:%Y%m%d-%H%M%S}"
    json_path = report_dir / f"{base}.json"
    md_path = report_dir / f"{base}.md"

    payload = {"fecha": now.isoformat(timespec="seconds"), **asdict(report)}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    sensitivity = (
        f"{report.sensitivity_cm_per_unit:.3f} cm/unidad"
        if report.sensitivity_cm_per_unit is not None
        else "no medida (ya estaba en tolerancia)"
    )
    lines = [
        "# Reporte de calibración de antenna delay — DWM3001CDK",
        "",
        f"> **Placa calibrada:** {report.device_port} · **Referencia:** "
        f"{report.reference_port} · **Fecha:** {now:%Y-%m-%d %H:%M:%S}",
        f"> **Clave:** `{report.key}` · **Distancia real:** {report.real_distance_cm:.1f} cm",
        "",
        f"**Resultado:** {'CONVERGIÓ' if report.converged else 'NO convergió'} · "
        f"delay {report.initial_delay} → **{report.final_delay}** · "
        f"sensibilidad {sensitivity} · "
        f"{'guardado en NVM (SAVE)' if report.saved else 'SIN guardar (--no-save)'}",
        "",
        "| Iter | Delay | Media [cm] | Desvío [cm] | Error [cm] | Corrección aplicada |",
        "|---|---|---|---|---|---|",
    ]
    for iteration in report.iterations:
        correction = (
            f"{iteration.correction_units:+d}" if iteration.correction_units is not None else "—"
        )
        lines.append(
            f"| {iteration.index} | {iteration.delay} | {iteration.mean_cm:.1f} "
            f"| {iteration.std_cm:.1f} | {iteration.error_cm:+.1f} | {correction} |"
        )
    lines += [
        "",
        "> **Verificación de persistencia (manual):** quitar y reponer la alimentación "
        "de la placa calibrada y confirmar con `LISTCAL` que la clave conserva el valor "
        "final (guía §4.3).",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path
