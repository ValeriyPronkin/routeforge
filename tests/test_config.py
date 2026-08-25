import os
from pathlib import Path

import pytest

from routeforge.config import Settings


def test_defaults_are_usable_without_a_file():
    s = Settings()
    assert s.app_title == "routeforge"
    assert s.distance_method == "haversine"


def test_title_comes_from_yaml(tmp_path):
    """Заголовок должен меняться настройками, а не правкой кода."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "common:\n"
        "    app_title: 'Логистика ООО Ромашка'\n"
        "    app_subtitle: 'план на завтра'\n",
        encoding="utf-8",
    )
    s = Settings.load(cfg)
    assert s.app_title == "Логистика ООО Ромашка"
    assert s.app_subtitle == "план на завтра"


def test_missing_file_falls_back_to_defaults(tmp_path):
    s = Settings.load(tmp_path / "нет-такого.yaml")
    assert s.app_title == "routeforge"


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("common:\n    app_title: 'из файла'\n", encoding="utf-8")
    monkeypatch.setenv("ROUTEFORGE_APP_TITLE", "из окружения")
    assert Settings.load(cfg).app_title == "из окружения"


def test_numeric_fields_are_converted_from_strings(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("common:\n    vehicle_capacity: '7500'\n", encoding="utf-8")
    s = Settings.load(cfg)
    assert s.vehicle_capacity == 7500 and isinstance(s.vehicle_capacity, int)


def test_paths_become_path_objects(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("common:\n    log_dir: 'var/log'\n", encoding="utf-8")
    assert Settings.load(cfg).log_dir == Path("var/log")


def test_secrets_are_not_exposed_by_to_dict(monkeypatch):
    monkeypatch.setenv("YANDEX_API_KEY", "секрет")
    s = Settings.load()
    assert s.yandex_api_key == "секрет"
    assert "yandex_api_key" not in s.to_dict()
