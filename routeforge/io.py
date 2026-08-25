"""Чтение и запись данных.

Формат входа намеренно простой: таблица с координатами и спросом.
Понимает csv и xlsx, распознаёт русские и английские названия колонок,
проверяет координаты — потому что в реальных выгрузках широта и долгота
регулярно приезжают строками с запятой вместо точки, пустыми или
перепутанными местами.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

#: Синонимы колонок. Ключ — каноническое имя, значение — что может прийти.
#:
#: Список намеренно универсальный: ни одно имя не привязано к отрасли.
#: Свои заголовки добавляются на месте, до чтения файлов::
#:
#:     from routeforge.io import COLUMN_ALIASES
#:     COLUMN_ALIASES["demand"] += ("отгрузка, паллет",)
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "код", "номер", "name", "название"),
    "lat": ("lat", "latitude", "широта", "y"),
    "lon": ("lon", "lng", "longitude", "долгота", "x"),
    "demand": ("demand", "спрос", "вес", "масса", "объём", "объем", "количество"),
}


class ValidationError(ValueError):
    """Данные не годятся для расчёта."""


def _to_float(series: pd.Series) -> pd.Series:
    """Приводит колонку к float, переживая ``'55,7558'`` и пробелы."""
    if series.dtype.kind in "fi":
        return series.astype(float)
    return pd.to_numeric(
        series.astype(str).str.strip().str.replace(" ", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    )


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Переименовывает колонки в канонические ``id/lat/lon/demand``."""
    lowered = {str(c).strip().lower(): c for c in df.columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                mapping[lowered[alias]] = canonical
                break
    return df.rename(columns=mapping)


def read_points(
    path: str | Path,
    *,
    sheet: str | int = 0,
    drop_invalid: bool = True,
) -> pd.DataFrame:
    """Читает точки из csv или xlsx.

    Возвращает DataFrame с колонками ``id``, ``lat``, ``lon``, ``demand``.

    :param drop_invalid: выбрасывать строки с непригодными координатами.
        При ``False`` такие строки вызовут :class:`ValidationError`.
    """
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path, sheet_name=sheet)
    else:
        df = pd.read_csv(path)

    df = normalize_columns(df)
    missing = {"lat", "lon"} - set(df.columns)
    if missing:
        raise ValidationError(
            f"В файле {path.name} нет колонок {sorted(missing)}. "
            f"Найдено: {list(df.columns)}. Ожидаются широта/долгота "
            f"под любым из имён {COLUMN_ALIASES['lat']} / {COLUMN_ALIASES['lon']}."
        )

    df["lat"] = _to_float(df["lat"])
    df["lon"] = _to_float(df["lon"])
    if "demand" in df.columns:
        df["demand"] = _to_float(df["demand"]).fillna(0.0)
    else:
        df["demand"] = 0.0
    if "id" not in df.columns:
        df["id"] = np.arange(len(df))

    bad = ~coordinates_valid(df["lat"], df["lon"])
    if bad.any():
        if not drop_invalid:
            raise ValidationError(
                f"{int(bad.sum())} строк с непригодными координатами, первые индексы: "
                f"{df.index[bad][:5].tolist()}"
            )
        df = df.loc[~bad]

    return df[["id", "lat", "lon", "demand"]].reset_index(drop=True)


def coordinates_valid(lat: pd.Series, lon: pd.Series) -> pd.Series:
    """Координаты в допустимых пределах и не в «нулевом острове»."""
    ok = lat.between(-90, 90) & lon.between(-180, 180)
    ok &= lat.notna() & lon.notna()
    # (0, 0) в Гвинейском заливе — почти всегда признак незаполненной строки,
    # а не точки в океане.
    ok &= ~((lat.abs() < 1e-9) & (lon.abs() < 1e-9))
    return ok


def coords_array(df: pd.DataFrame) -> list[tuple[float, float]]:
    """Список ``(lat, lon)`` из нормализованного DataFrame."""
    return list(df[["lat", "lon"]].itertuples(index=False, name=None))


def write_routes(
    df: pd.DataFrame,
    path: str | Path,
    *,
    sheet_name: str = "routes",
) -> Path:
    """Сохраняет результат в xlsx или csv по расширению пути."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df.to_excel(path, sheet_name=sheet_name, index=False)
    else:
        df.to_csv(path, index=False)
    return path
