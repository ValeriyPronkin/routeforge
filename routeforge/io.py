"""Чтение и запись данных.

Формат входа намеренно простой: таблица с координатами и спросом.
Понимает csv и xlsx, распознаёт русские и английские названия колонок,
проверяет координаты — потому что в реальных выгрузках широта и долгота
регулярно приезжают строками с запятой вместо точки, пустыми или
перепутанными местами.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO, Any

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


#: Синонимы колонок файла баз. Отдельно от точек: у базы имя и код — разные
#: колонки, а в файле точек ``name`` идёт синонимом ``id``.
DEPOT_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "код", "номер"),
    "name": ("name", "название", "наименование", "имя"),
    "lat": ("lat", "latitude", "широта", "y"),
    "lon": ("lon", "lng", "longitude", "долгота", "x"),
    "capacity": ("capacity", "мощность", "вместимость", "лимит"),
}


#: Синонимы колонок файла машин. Номер здесь обязателен по смыслу, а не по
#: проверке: без него в отчёте остаётся индекс, по которому машину не найти.
VEHICLE_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "номер", "гос. номер", "гос.номер", "госномер", "гос номер",
           "№ п/п", "код"),
    "capacity": ("capacity", "вместимость", "грузоподъёмность", "грузоподъемность",
                 "объем кузова", "объём кузова", "объем (вместимость) кузова"),
    "max_time_min": ("max_time_min", "смена", "смена, мин", "время работы",
                     "рабочее время", "максимальное время работы"),
    "depot": ("depot", "база", "гараж", "индекс базы"),
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


def normalize_columns(
    df: pd.DataFrame,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> pd.DataFrame:
    """Переименовывает колонки в канонические ``id/lat/lon/demand``.

    :param aliases: своя таблица синонимов; по умолчанию — для файла точек.
    """
    lowered = {str(c).strip().lower(): c for c in df.columns}
    mapping: dict[str, str] = {}
    for canonical, names in (aliases or COLUMN_ALIASES).items():
        for alias in names:
            if alias in lowered:
                mapping[lowered[alias]] = canonical
                break
    return df.rename(columns=mapping)


def _source_name(source: Any) -> str:
    """Имя источника для сообщений об ошибке.

    Для пути — имя файла, для загруженного через интерфейс объекта — его
    атрибут ``name``, который проставляет streamlit.
    """
    if isinstance(source, (str, Path)):
        return Path(source).name
    return str(getattr(source, "name", "переданные данные"))


def _read_table(source: Any, name: str, sheet: str | int) -> pd.DataFrame:
    """Читает csv или xlsx, определяя формат по имени источника."""
    try:
        if name.lower().endswith((".xlsx", ".xls")):
            return pd.read_excel(source, sheet_name=sheet)
        df = pd.read_csv(source)
        # Excel в русской локали сохраняет csv с точкой с запятой, и тогда вся
        # строка приезжает одной колонкой вида 'id;lat;lon'. Разделитель
        # подбираем только в этом случае: разбор наугад медленнее и иногда
        # ошибается, а нормальную запятую портить незачем.
        if len(df.columns) == 1 and any(c in str(df.columns[0]) for c in ";\t|"):
            if hasattr(source, "seek"):
                source.seek(0)
            df = pd.read_csv(source, sep=None, engine="python")
        return df
    except FileNotFoundError:
        raise
    except Exception as exc:  # битый файл, чужой разделитель, не та кодировка
        # Наружу это должно выходить как «данные не годятся», иначе интерфейс
        # покажет пользователю трассировку pandas вместо понятной причины.
        raise ValidationError(f"Не удалось прочитать {name}: {exc}") from exc


def read_points(
    source: str | Path | IO[bytes],
    *,
    sheet: str | int = 0,
    drop_invalid: bool = True,
) -> pd.DataFrame:
    """Читает точки из csv или xlsx.

    Возвращает DataFrame с колонками ``id``, ``lat``, ``lon``, ``demand``.

    :param source: путь к файлу либо открытый файловый объект. Второе — это
        то, что отдаёт ``st.file_uploader``: у него нет пути на диске, только
        имя и содержимое, поэтому формат определяется по имени.
    :param drop_invalid: выбрасывать строки с непригодными координатами.
        При ``False`` такие строки вызовут :class:`ValidationError`.
    """
    name = _source_name(source)
    df = normalize_columns(_read_table(source, name, sheet))
    missing = {"lat", "lon"} - set(df.columns)
    if missing:
        raise ValidationError(
            f"В файле {name} нет колонок {sorted(missing)}. "
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


def read_depots(
    source: str | Path | IO[bytes],
    *,
    sheet: str | int = 0,
) -> pd.DataFrame:
    """Читает базы из csv или xlsx.

    Возвращает DataFrame с колонками ``id``, ``name``, ``lat``, ``lon``,
    ``capacity``. Пустая мощность означает «не ограничена».

    В отличие от точек, строка с непригодными координатами не отбрасывается,
    а считается ошибкой: потерять точку из тысячи — полбеды, а молча потерять
    базу значит посчитать не ту задачу.
    """
    name = _source_name(source)
    df = normalize_columns(_read_table(source, name, sheet), DEPOT_COLUMN_ALIASES)

    missing = {"lat", "lon"} - set(df.columns)
    if missing:
        raise ValidationError(
            f"В файле баз {name} нет колонок {sorted(missing)}. "
            f"Найдено: {list(df.columns)}. Ожидаются широта/долгота под любым "
            f"из имён {DEPOT_COLUMN_ALIASES['lat']} / {DEPOT_COLUMN_ALIASES['lon']}."
        )

    df = df.copy()
    df["lat"] = _to_float(df["lat"])
    df["lon"] = _to_float(df["lon"])
    if df.empty:
        raise ValidationError(f"В файле баз {name} нет ни одной строки.")

    bad = ~coordinates_valid(df["lat"], df["lon"])
    if bad.any():
        raise ValidationError(
            f"В файле баз {name} {int(bad.sum())} строк с непригодными "
            f"координатами, первые индексы: {df.index[bad][:5].tolist()}"
        )

    df["capacity"] = _to_float(df["capacity"]) if "capacity" in df.columns else np.nan
    if "id" not in df.columns:
        df["id"] = [f"D{i}" for i in range(len(df))]
    if "name" not in df.columns:
        df["name"] = df["id"].astype(str)
    df["name"] = df["name"].fillna(df["id"].astype(str)).astype(str)

    return df[["id", "name", "lat", "lon", "capacity"]].reset_index(drop=True)


def read_vehicles(
    source: str | Path | IO[bytes],
    *,
    sheet: str | int = 0,
) -> pd.DataFrame:
    """Читает реестр машин из csv или xlsx.

    Возвращает DataFrame с колонками ``id``, ``capacity``, ``max_time_min``,
    ``depot``. Одна строка — одна машина: парк разнородный, у каждой своя
    вместимость.

    Обязательна только вместимость. Пустая смена означает «как в настройках
    расчёта», пустая база — «машина доступна любой базе». Номера, если
    колонки нет, проставляются как ``ТС 0``, ``ТС 1`` — но лучше, чтобы он
    был настоящим: номер идёт до таблицы маршрутов.
    """
    name = _source_name(source)
    df = normalize_columns(_read_table(source, name, sheet), VEHICLE_COLUMN_ALIASES)

    if "capacity" not in df.columns:
        raise ValidationError(
            f"В файле машин {name} нет колонки вместимости. "
            f"Найдено: {list(df.columns)}. Ожидается одно из имён "
            f"{VEHICLE_COLUMN_ALIASES['capacity']}."
        )

    df = df.copy()
    if df.empty:
        raise ValidationError(f"В файле машин {name} нет ни одной строки.")

    df["capacity"] = _to_float(df["capacity"])
    bad = ~(df["capacity"] > 0)
    if bad.any():
        # Машина с нулевой или пустой вместимостью не увезёт ничего, а в
        # расчёте будет выглядеть полноценной. Это ошибка файла, а не строка
        # на выброс: парк собирают руками, и молча терять из него машину хуже,
        # чем не посчитать вовсе.
        raise ValidationError(
            f"В файле машин {name} {int(bad.sum())} строк без вместимости, "
            f"первые индексы: {df.index[bad][:5].tolist()}"
        )

    df["max_time_min"] = _to_float(df["max_time_min"]) if "max_time_min" in df.columns else np.nan
    df["depot"] = _to_float(df["depot"]) if "depot" in df.columns else np.nan
    if "id" not in df.columns:
        df["id"] = [f"ТС {i}" for i in range(len(df))]
    df["id"] = df["id"].astype(str)

    return df[["id", "capacity", "max_time_min", "depot"]].reset_index(drop=True)


def depot_capacities(depots: pd.DataFrame) -> list[float] | None:
    """Мощности для :func:`routeforge.pipeline.plan_routes`.

    ``None``, если мощность не задана ни у одной базы — тогда ограничение не
    применяется вовсе. Если задана хотя бы у одной, у остальных она считается
    бесконечной: пустая клетка значит «не ограничена», а не «ноль».
    """
    capacity = depots["capacity"]
    if capacity.isna().all():
        return None
    return capacity.fillna(float("inf")).tolist()


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


def write_report(
    tables: dict[str, pd.DataFrame],
    target: str | Path | IO[bytes],
) -> None:
    """Пишет отчёт из нескольких листов в один xlsx.

    Отчёт по расчёту — это не одна таблица: маршруты отвечают на вопрос
    «куда ехать», сводка по машинам — «чем занят парк», и разносить их по
    файлам неудобно тому, кто потом с ними работает.

    :param tables: имя листа -> таблица. Порядок сохраняется.
    :param target: путь или открытый файловый объект. Второе нужно, чтобы
        отдать отчёт на скачивание, не создавая файла на диске.
    """
    if not tables:
        raise ValueError("отчёт без таблиц")
    if isinstance(target, (str, Path)):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        for sheet, table in tables.items():
            # Лист без строк Excel всё равно должен показать заголовки:
            # пустая сводка это тоже ответ, а не сломанный файл.
            table.to_excel(writer, sheet_name=sheet[:31], index=False)


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
