import pandas as pd
import pytest

from routeforge.io import ValidationError, coordinates_valid, normalize_columns, read_points


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
