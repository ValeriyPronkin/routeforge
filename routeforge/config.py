"""Настройки: значения по умолчанию, файл конфигурации, переменные окружения.

Приоритет — от низкого к высокому: значения по умолчанию, ``config.yaml``,
переменные окружения. Секреты живут только в окружении, в yaml их быть
не должно.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


@dataclass
class Settings:
    """Параметры расчёта."""

    #: Адрес OSRM. Пустая строка — работать только по haversine.
    osrm_url: str = "http://localhost:5000"
    #: Способ расчёта расстояний: ``haversine`` или ``osrm``.
    distance_method: str = "haversine"
    #: Максимум точек в кластере перед передачей солверу.
    max_sites_per_cluster: int = 500
    #: Параметры парка по умолчанию.
    vehicle_capacity: int = 10_000
    vehicle_max_time_min: int = 8 * 60
    vehicle_speed_kmh: float = 40.0
    service_time_min: int = 10
    #: Сколько секунд солверу на один кластер.
    solver_time_limit_s: int = 30
    #: Сколько машин можно добавить сверх расчётной оценки, если солвер иначе
    #: бросает точки. Ноль отключает добор: тогда лишние точки останутся
    #: необслуженными, но парк не вырастет.
    auto_add_vehicles: int = 2
    #: Штраф за пропуск точки, в метрах. Должен заметно превышать крюк до
    #: самой дальней точки, иначе бросить её солверу выгоднее, чем заехать.
    #: Помогает только против пропусков «по расчёту»; против нехватки
    #: вместимости или времени смены он бессилен, см. docs/algorithm.md.
    drop_penalty: int = 1_000_000
    #: Заголовок и подзаголовок приложения. Вынесены в настройки, чтобы
    #: инструмент можно было назвать под свою задачу, не трогая код.
    app_title: str = "routeforge"
    app_subtitle: str = "Распределение точек по базам, кластеризация и построение маршрутов"
    #: Каталоги ввода-вывода.
    input_dir: Path = field(default_factory=lambda: Path("data/input"))
    output_dir: Path = field(default_factory=lambda: Path("data/output"))
    #: Куда писать журнал работы.
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    log_level: str = "INFO"
    #: Ключ геокодера Яндекса. Только из окружения, никогда из файла.
    yandex_api_key: str | None = None

    @classmethod
    def load(cls, path: str | Path | None = None, **overrides: Any) -> "Settings":
        """Собирает настройки из файла и окружения."""
        data: dict[str, Any] = {}

        if path is not None:
            path = Path(path)
            if path.exists():
                if yaml is None:
                    raise RuntimeError("Для чтения yaml нужен пакет PyYAML или omegaconf")
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                data.update(loaded.get("common", loaded))

        known = {f.name for f in fields(cls)}
        for name in known:
            env = os.environ.get(f"ROUTEFORGE_{name.upper()}")
            if env is not None:
                data[name] = env
        if os.environ.get("YANDEX_API_KEY"):
            data["yandex_api_key"] = os.environ["YANDEX_API_KEY"]

        data.update(overrides)
        data = {k: v for k, v in data.items() if k in known}

        # Приведение типов: из yaml и окружения всё приходит строками.
        for f in fields(cls):
            if f.name not in data or data[f.name] is None:
                continue
            value = data[f.name]
            if f.type in {"int", int}:
                data[f.name] = int(value)
            elif f.type in {"float", float}:
                data[f.name] = float(value)
            elif f.name in {"input_dir", "output_dir", "log_dir"}:
                data[f.name] = Path(value)
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """Настройки без секретов — пригодно для логирования."""
        data = asdict(self)
        data.pop("yandex_api_key", None)
        data["input_dir"] = str(self.input_dir)
        data["output_dir"] = str(self.output_dir)
        data["log_dir"] = str(self.log_dir)
        return data
