import io

import pandas as pd
import pytest

from routeforge.io import (
    ValidationError,
    coordinates_valid,
    depot_capacities,
    normalize_columns,
    read_depots,
    read_points,
)


def test_reads_csv_with_russian_headers(tmp_path):
    path = tmp_path / "points.csv"
    path.write_text("Id,Широта,Долгота,Спрос\n1,55.75,37.61,120\n", encoding="utf-8")
    df = read_points(path)
    assert list(df.columns) == ["id", "lat", "lon", "demand"]
    assert df.loc[0, "lat"] == pytest.approx(55.75)
    assert df.loc[0, "demand"] == pytest.approx(120)


def test_reads_csv_with_english_headers(tmp_path):
    path = tmp_path / "points.csv"
    path.write_text("id,latitude,longitude,demand\nA,55.75,37.61,5\n", encoding="utf-8")
    assert read_points(path).loc[0, "lon"] == pytest.approx(37.61)


def test_parses_decimal_comma(tmp_path):
    # Выгрузки из Excel регулярно приносят '55,7558' вместо '55.7558'.
    path = tmp_path / "points.csv"
    path.write_text('id,широта,долгота\n1,"55,7558","37,6176"\n', encoding="utf-8")
    df = read_points(path)
    assert df.loc[0, "lat"] == pytest.approx(55.7558)


def test_drops_rows_with_broken_coordinates(tmp_path):
    path = tmp_path / "points.csv"
    path.write_text("id,lat,lon\n1,55.75,37.61\n2,,\n3,0,0\n4,999,37\n", encoding="utf-8")
    assert len(read_points(path)) == 1


def test_raises_when_asked_not_to_drop(tmp_path):
    path = tmp_path / "points.csv"
    path.write_text("id,lat,lon\n1,55.75,37.61\n2,999,37\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="непригодными"):
        read_points(path, drop_invalid=False)


def test_missing_coordinate_columns_gives_actionable_error(tmp_path):
    path = tmp_path / "points.csv"
    path.write_text("id,address\n1,Москва\n", encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        read_points(path)
    assert "address" in str(exc.value)


def test_demand_defaults_to_zero(tmp_path):
    path = tmp_path / "points.csv"
    path.write_text("id,lat,lon\n1,55.75,37.61\n", encoding="utf-8")
    assert read_points(path).loc[0, "demand"] == 0.0


def test_unrecognised_demand_column_silently_becomes_zero(tmp_path):
    """Ловушка, о которой надо знать: маршрутизация без спроса — допустимый
    сценарий, поэтому нераспознанный заголовок не ошибка, а ноль. Но если
    колонка спроса в файле есть и просто названа непривычно, все точки
    получат ноль, и солвер построит один маршрут на всё."""
    path = tmp_path / "points.csv"
    path.write_text("id,lat,lon,отгрузка\n1,55.75,37.61,900\n", encoding="utf-8")
    assert read_points(path).loc[0, "demand"] == 0.0


def test_custom_alias_can_be_registered(tmp_path):
    from routeforge.io import COLUMN_ALIASES

    path = tmp_path / "points.csv"
    path.write_text("id,lat,lon,отгрузка\n1,55.75,37.61,900\n", encoding="utf-8")
    original = COLUMN_ALIASES["demand"]
    try:
        COLUMN_ALIASES["demand"] = original + ("отгрузка",)
        assert read_points(path).loc[0, "demand"] == 900.0
    finally:
        COLUMN_ALIASES["demand"] = original


def test_null_island_is_rejected():
    ok = coordinates_valid(pd.Series([0.0, 55.0]), pd.Series([0.0, 37.0]))
    assert ok.tolist() == [False, True]


def test_normalize_is_case_insensitive():
    df = pd.DataFrame(columns=["ID", "ШИРОТА", "Долгота"])
    assert set(normalize_columns(df).columns) == {"id", "lat", "lon"}


class UploadedFile(io.BytesIO):
    """Подделка под то, что отдаёт ``st.file_uploader``.

    Это не путь на диске, а содержимое в памяти с именем: ровно на этом
    read_points и падал, потому что первым делом делал ``Path(path)``.
    """

    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def test_reads_csv_uploaded_through_the_interface():
    uploaded = UploadedFile(
        "id,широта,долгота,спрос\n1,55.75,37.61,120\n".encode("utf-8"), "точки.csv"
    )
    df = read_points(uploaded)
    assert len(df) == 1
    assert df.loc[0, "lat"] == pytest.approx(55.75)


def test_reads_xlsx_uploaded_through_the_interface(tmp_path):
    # Формат определяется по имени: у загруженного файла нет пути на диске.
    source = tmp_path / "points.xlsx"
    pd.DataFrame([{"id": 1, "lat": 55.75, "lon": 37.61, "demand": 120}]).to_excel(
        source, index=False
    )
    df = read_points(UploadedFile(source.read_bytes(), "точки.xlsx"))
    assert df.loc[0, "demand"] == pytest.approx(120)


def test_unreadable_file_gives_validation_error_not_traceback():
    """Битый файл обязан выходить наружу как «данные не годятся».

    Интерфейс ловит ValidationError и показывает причину; всё остальное
    вывалится пользователю трассировкой pandas.
    """
    with pytest.raises(ValidationError, match="Не удалось прочитать"):
        read_points(UploadedFile(b"PK\x03\x04\x00\x00 not a table", "otchet.xlsx"))


def test_error_message_names_the_uploaded_file():
    with pytest.raises(ValidationError) as exc:
        read_points(UploadedFile(b"id,address\n1,Moscow\n", "adresa.csv"))
    assert "adresa.csv" in str(exc.value)


def test_reads_semicolon_csv_from_russian_excel():
    """Excel в русской локали пишет csv через точку с запятой.

    Без подбора разделителя весь файл приезжает одной колонкой 'id;lat;lon',
    и пользователь получает «нет колонок lat, lon» на совершенно нормальном
    файле — сообщение верное, но причина в нём не названа.
    """
    content = 'id;широта;долгота;спрос\n1;"55,7558";"37,6176";120\n'.encode("utf-8")
    df = read_points(UploadedFile(content, "iz_excel.csv"))
    assert df.loc[0, "lat"] == pytest.approx(55.7558)
    assert df.loc[0, "demand"] == pytest.approx(120)


def test_comma_csv_is_not_broken_by_separator_detection(tmp_path):
    path = tmp_path / "points.csv"
    path.write_text("id,lat,lon,demand\n1,55.75,37.61,120\n", encoding="utf-8")
    assert read_points(path).loc[0, "lon"] == pytest.approx(37.61)


def test_reads_depots_with_russian_headers():
    content = (
        "код;название;широта;долгота;мощность\n"
        'D0;Склад Север;"55,31";"61,48";60000\n'
    ).encode("utf-8")
    depots = read_depots(UploadedFile(content, "bazy.csv"))
    assert list(depots.columns) == ["id", "name", "lat", "lon", "capacity"]
    assert depots.loc[0, "name"] == "Склад Север"
    assert depots.loc[0, "lat"] == pytest.approx(55.31)
    assert depots.loc[0, "capacity"] == pytest.approx(60000)


def test_depot_name_falls_back_to_id():
    depots = read_depots(UploadedFile(b"id,lat,lon\nD7,55.31,61.48\n", "bazy.csv"))
    assert depots.loc[0, "name"] == "D7"


def test_depots_without_capacity_are_unlimited():
    """Пустая мощность значит «не ограничена», а не «ноль».

    Иначе файл баз без этой колонки — а её знают не всегда — приводил бы к
    тому, что ни одна база не приняла бы ни одной точки.
    """
    depots = read_depots(UploadedFile(b"id,lat,lon\nD0,55.31,61.48\n", "bazy.csv"))
    assert depot_capacities(depots) is None


def test_partial_capacity_leaves_the_rest_unlimited():
    content = b"id,lat,lon,capacity\nD0,55.31,61.48,5000\nD1,55.05,61.32,\n"
    depots = read_depots(UploadedFile(content, "bazy.csv"))
    assert depot_capacities(depots) == [5000.0, float("inf")]


def test_broken_depot_coordinates_are_an_error_not_a_drop():
    """Точку из тысячи потерять полбеды, базу — значит посчитать не ту задачу."""
    content = b"id,lat,lon\nD0,55.31,61.48\nD1,,\n"
    with pytest.raises(ValidationError, match="непригодными"):
        read_depots(UploadedFile(content, "bazy.csv"))


def test_depots_file_without_coordinates_gives_actionable_error():
    with pytest.raises(ValidationError) as exc:
        read_depots(UploadedFile(b"id,address\nD0,Chelyabinsk\n", "bazy.csv"))
    assert "файле баз" in str(exc.value)
    assert "address" in str(exc.value)
