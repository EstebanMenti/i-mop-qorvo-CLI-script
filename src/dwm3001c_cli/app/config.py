"""Carga de configuración con precedencia CLI > YAML > defaults (plan §7).

El archivo YAML opcional (``--config archivo.yaml``) solo puede contener
claves que también existan como opciones de línea de comandos: cualquier
otra clave es un error, para detectar temprano archivos mal escritos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml_config(path: Path | None, *, allowed_keys: set[str]) -> dict[str, Any]:
    """Carga un archivo YAML de configuración, si se indicó uno.

    Args:
        path: ruta al YAML, o ``None`` si no se pasó ``--config``.
        allowed_keys: nombres de opciones de CLI válidos para este subcomando.

    Raises:
        ValueError: el YAML no es un mapeo, o contiene claves fuera de
            ``allowed_keys``.
    """
    if path is None:
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: el YAML de configuración debe ser un mapeo clave-valor")
    unknown = set(raw) - allowed_keys
    if unknown:
        raise ValueError(
            f"{path}: claves desconocidas {sorted(unknown)}; válidas: {sorted(allowed_keys)}"
        )
    return raw


def resolve_option(name: str, cli_value: Any, yaml_config: dict[str, Any], default: Any) -> Any:
    """Resuelve una opción con precedencia CLI > YAML > default.

    ``cli_value`` distinto de ``None`` gana siempre (el usuario lo tipeó
    explícitamente). Si no, se busca en ``yaml_config``; si tampoco está,
    se usa ``default``.
    """
    if cli_value is not None:
        return cli_value
    if name in yaml_config:
        return yaml_config[name]
    return default
