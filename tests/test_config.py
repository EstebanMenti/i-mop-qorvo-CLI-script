"""Tests de la carga de configuración con precedencia CLI > YAML > default (fase F5)."""

from pathlib import Path

import pytest

from dwm3001c_cli.app.config import load_yaml_config, resolve_option


class TestLoadYamlConfig:
    def test_none_path_returns_empty(self) -> None:
        assert load_yaml_config(None, allowed_keys={"port"}) == {}

    def test_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("port: COM7\nsamples: 50\n", encoding="utf-8")

        config = load_yaml_config(path, allowed_keys={"port", "samples"})

        assert config == {"port": "COM7", "samples": 50}

    def test_non_dict_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("- solo\n- una\n- lista\n", encoding="utf-8")

        with pytest.raises(ValueError, match="mapeo"):
            load_yaml_config(path, allowed_keys={"port"})

    def test_unknown_key_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("port: COM7\nrestore: true\n", encoding="utf-8")

        with pytest.raises(ValueError, match="restore"):
            load_yaml_config(path, allowed_keys={"port"})

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("", encoding="utf-8")

        assert load_yaml_config(path, allowed_keys={"port"}) == {}


class TestResolveOption:
    def test_cli_value_wins(self) -> None:
        assert resolve_option("port", "COM7", {"port": "COM8"}, "COM9") == "COM7"

    def test_yaml_wins_when_no_cli_value(self) -> None:
        assert resolve_option("port", None, {"port": "COM8"}, "COM9") == "COM8"

    def test_default_when_neither(self) -> None:
        assert resolve_option("port", None, {}, "COM9") == "COM9"

    def test_falsy_cli_value_is_not_treated_as_missing(self) -> None:
        # 0 y False son valores válidos, no "ausentes": solo None dispara el fallback.
        assert resolve_option("no_save", False, {"no_save": True}, False) is False
        assert resolve_option("channel", 0, {"channel": 9}, 9) == 0
