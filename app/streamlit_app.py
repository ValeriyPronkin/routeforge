"""Интерактивная демонстрация routeforge.

    streamlit run app/streamlit_app.py

Приложение намеренно небольшое: показать пайплайн и дать покрутить
параметры. Вся логика живёт в пакете, здесь только интерфейс.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

from routeforge.config import Settings  # noqa: E402
from routeforge.distance import Coord  # noqa: E402
from routeforge.io import (  # noqa: E402
    ValidationError,
    depot_capacities,
    read_depots,
    read_points,
)
from routeforge.logs import setup_logging  # noqa: E402
from routeforge.pipeline import PlanResult, plan_routes_sync  # noqa: E402
from routeforge.polylines import straight_polylines  # noqa: E402
from routeforge.viz import color_for, plot_depots, plot_routes, plot_sites  # noqa: E402

# Настройки читаются из config.yaml рядом с проектом; переменные окружения
# с префиксом ROUTEFORGE_ имеют приоритет. Числовые параметры расчёта ниже
# задаются виджетами, а имя и журнал берутся отсюда.
BASE = Settings.load(ROOT / "config.yaml")
LOG_PATH = setup_logging(BASE)

st.set_page_config(page_title=BASE.app_title, page_icon="🚚", layout="wide")

#: folium понимает несколько имён, которых нет в CSS, — для плашек в таблице
#: нужен цвет, который поймёт браузер.
CSS_COLOR = {"darkpurple": "#5b2c6f", "cadetblue": "#5f9ea0", "beige": "#d8c9a3"}


@dataclass
class RouteRow:
    """Строка результата вместе с её геометрией и цветом на карте.

    Таблица и карта строятся из одного списка — иначе цвета в них
    разъезжаются при первом же изменении порядка.
    """

    depot: int
    cluster: int
    vehicle: int
    stops: int
    distance_km: float
    duration_min: int
    load: int
    polyline: list[Coord]
    colour: str

    @property
    def label(self) -> str:
        return f"База {self.depot} · кластер {self.cluster} · машина {self.vehicle}"


def build_rows(result: PlanResult, depots: list[Coord]) -> list[RouteRow]:
    rows: list[RouteRow] = []
    for (depot_id, cluster_id), solution in sorted(result.solutions.items()):
        chunk = result.sites[
            (result.sites["depot"] == depot_id) & (result.sites["cluster"] == cluster_id)
        ]
        nodes = [depots[depot_id], *chunk[["lat", "lon"]].itertuples(index=False, name=None)]
        lines = straight_polylines([r.nodes for r in solution.routes], nodes)
        for route, line in zip(solution.routes, lines):
            rows.append(
                RouteRow(
                    depot=depot_id,
                    cluster=cluster_id,
                    vehicle=route.vehicle,
                    stops=max(0, len(route.nodes) - 2),
                    distance_km=round(route.distance_m / 1000, 2),
                    duration_min=route.duration_min,
                    load=route.load,
                    polyline=line,
                    colour=color_for(len(rows)),
                )
            )
    return rows


@st.cache_data
def load_sample() -> tuple[pd.DataFrame, pd.DataFrame]:
    sites = read_points(ROOT / "data/sample/sites.csv")
    depots = read_depots(ROOT / "data/sample/depots.csv")
    return sites, depots


def metrics_row(pairs: list[tuple[str, str]]) -> None:
    """Метрики в одну строку. Вертикальной колонкой они занимают пол-экрана
    и перетягивают внимание на себя, хотя главное здесь — карта."""
    for column, (label, value) in zip(st.columns(len(pairs)), pairs):
        column.metric(label, value)


def spaced(number: float) -> str:
    return f"{number:,.0f}".replace(",", " ")


# ---------------------------------------------------------------- сайдбар
with st.sidebar:
    st.header("Данные")
    # Базы первыми: по ним точки и разбираются — сначала каждая приписывается
    # к базе, и только потом точки базы дробятся на кластеры.
    uploaded_depots = st.file_uploader("Базы (csv или xlsx)", type=["csv", "xlsx"])
    uploaded = st.file_uploader("Точки (csv или xlsx)", type=["csv", "xlsx"])
    st.caption(
        "Это две разные сущности: базы — откуда машина выезжает и куда "
        "возвращается, точки — что она объезжает. Структура обоих файлов "
        "описана в README, раздел «Входные файлы». Без своих файлов "
        "показывается демо-набор."
    )

    try:
        sample_sites, sample_depots = load_sample()
        sites = read_points(uploaded) if uploaded is not None else sample_sites
        depots_df = (
            read_depots(uploaded_depots) if uploaded_depots is not None else sample_depots
        )
    except (ValidationError, FileNotFoundError) as exc:
        st.error(str(exc))
        st.stop()

    if uploaded is not None and uploaded_depots is None:
        # Самый неприятный из возможных исходов — не ошибка, а ноль маршрутов:
        # свои точки считаются относительно демонстрационных баз, до которых
        # не доехать за смену. Молчать об этом нельзя.
        st.warning(
            "Точки ваши, а базы демонстрационные — под Челябинском. Если ваши "
            "точки в другом регионе, маршруты не построятся: до базы не "
            "доехать за смену. Загрузите файл баз."
        )

    depots: list[Coord] = list(depots_df[["lat", "lon"]].itertuples(index=False, name=None))
    depot_names = depots_df["name"].tolist()
    capacities = depot_capacities(depots_df)

    st.header("Расстояния")
    method = st.radio(
        "Способ расчёта",
        ["haversine", "osrm"],
        help="haversine — по прямой, работает сразу. osrm — по дорогам, нужен сервер.",
    )
    osrm_url = st.text_input("Адрес OSRM", "http://localhost:5000", disabled=method != "osrm")

    st.header("Парк")
    capacity = st.number_input("Вместимость машины", 100, 100_000, 4000, step=100)
    shift = st.number_input("Смена, мин", 60, 1440, 480, step=30)
    speed = st.number_input("Средняя скорость, км/ч", 5.0, 120.0, 40.0, step=5.0)
    service = st.number_input("Время на точке, мин", 0, 120, 10)

    st.header("Расчёт")
    max_per_cluster = st.slider(
        "Точек в кластере", 20, 500, 60, step=10,
        help="Верхний предел размера группы, которую получает солвер. "
        "Больше — маршруты аккуратнее, но расчёт дольше.",
    )
    time_limit = st.slider("Секунд солверу на кластер", 1, 60, 5)

    run = st.button("Рассчитать", type="primary", width="stretch")
    st.caption(f"Журнал работы: `{Path(BASE.log_dir) / 'routeforge.log'}`")

# ---------------------------------------------------------------- шапка
st.title(BASE.app_title)
st.caption(BASE.app_subtitle)

with st.expander("Что означают «база», «кластер» и «машина»"):
    st.markdown(
        """
**База** — место, откуда машина выезжает и куда возвращается: склад,
распределительный центр, гараж, площадка выгрузки. В задаче маршрутизации
это депо. Баз может быть несколько, у каждой своя мощность — сколько она
способна принять или отгрузить за период.

**Кластер** — группа точек внутри одной базы. Решать маршруты сразу для
всех точек базы невыгодно: время расчёта растёт быстрее линейного, и одна
задача на 3000 точек считается дольше, чем десять по 300. Поэтому точки
базы дробятся на группы по числу из «Точек в кластере», и внутри каждой
маршруты считаются отдельно.

**Машина** — один маршрут: выезд с базы, объезд точек, возврат. Сколько
машин понадобится, определяется вместимостью и длительностью смены, а не
задаётся вручную.

Порядок такой: точки распределяются **по базам** (каждая уходит на
ближайшую, у которой хватает мощности), затем точки каждой базы дробятся
**на кластеры**, и уже внутри кластера строятся **маршруты машин**.
        """
    )

# ---------------------------------------------------------------- до расчёта
if not run:
    metrics_row(
        [
            ("Точек", spaced(len(sites))),
            ("Баз", str(len(depots))),
            ("Суммарный спрос", spaced(sites["demand"].sum())),
        ]
    )
    fmap = plot_sites(list(sites[["lat", "lon"]].itertuples(index=False, name=None)))
    plot_depots(depots, fmap=fmap, names=depot_names)
    # у st_folium width — это ширина в пикселях, а не режим,
    # поэтому здесь остаётся собственный флаг компонента
    st_folium(fmap, height=560, use_container_width=True, returned_objects=[])
    st.info("Нажмите «Рассчитать» в панели слева.")
    st.stop()

# ---------------------------------------------------------------- расчёт
settings = Settings(
    app_title=BASE.app_title,
    app_subtitle=BASE.app_subtitle,
    log_dir=BASE.log_dir,
    log_level=BASE.log_level,
    distance_method=method,
    osrm_url=osrm_url,
    max_sites_per_cluster=max_per_cluster,
    vehicle_capacity=int(capacity),
    vehicle_max_time_min=int(shift),
    vehicle_speed_kmh=float(speed),
    service_time_min=int(service),
    solver_time_limit_s=int(time_limit),
)

logger.info(
    "Расчёт: {} точек, {} баз, метод {}, вместимость {}, лимит солвера {} с",
    len(sites), len(depots), method, capacity, time_limit,
)
with st.spinner("Считаем маршруты…"):
    try:
        result = plan_routes_sync(
            sites, depots, settings=settings, depot_capacities=capacities
        )
    except RuntimeError as exc:
        logger.exception("Расчёт не удался")
        st.error(f"Не удалось получить расстояния: {exc}")
        st.caption(f"Подробности в журнале: `{LOG_PATH}`")
        st.stop()
logger.info(
    "Готово: {} кластеров, {} маршрутов, {:.1f} км, "
    "не приняли базы {}, бросил солвер {}",
    len(result.solutions), result.vehicles_used,
    result.total_distance_m / 1000, len(result.unassigned), len(result.dropped),
)

rows = build_rows(result, depots)

metrics_row(
    [
        ("Точек", spaced(len(sites))),
        ("Баз", str(len(depots))),
        ("Кластеров", str(len(result.solutions))),
        ("Маршрутов", str(len(rows))),
        ("Пробег, км", spaced(result.total_distance_m / 1000)),
        ("Не обслужено", str(len(result.unserved))),
    ]
)
# Два вида потерь лечатся по-разному, поэтому и показываются раздельно.
if result.unassigned:
    st.warning(
        f"{len(result.unassigned)} точек не приняла ни одна база: не хватило мощности. "
        "Лечится мощностью баз или их количеством."
    )
if result.dropped:
    st.warning(
        f"{len(result.dropped)} точек солвер оставил без маршрута: не хватило машин "
        "или времени смены. Увеличение штрафа тут обычно не помогает — "
        "поднимите смену или уменьшите размер кластера. "
        "Подробнее в docs/algorithm.md."
    )

# ---------------------------------------------------------------- карта
left, right = st.columns([3, 2])
with left:
    chosen = st.selectbox(
        "Показать маршруты", ["Все"] + [row.label for row in rows]
    )
with right:
    painting = st.radio(
        "Раскраска точек",
        ["по базам", "по кластерам"],
        horizontal=True,
        help="По базам — видно, какая точка к какой базе приписана. "
        "По кластерам — видно, как точки одной базы разбиты на группы, "
        "внутри которых и считаются маршруты.",
    )

visible = rows if chosen == "Все" else [r for r in rows if r.label == chosen]

if painting == "по базам":
    site_labels = result.sites["depot"].tolist()
else:
    # Метка кластера уникальна только внутри базы, поэтому пары
    # (база, кластер) нумеруются сквозным номером — иначе кластер 0
    # у всех баз получил бы один цвет.
    pairs = sorted(set(zip(result.sites["depot"], result.sites["cluster"])))
    order = {pair: i for i, pair in enumerate(pairs)}
    site_labels = [order[p] for p in zip(result.sites["depot"], result.sites["cluster"])]

fmap = plot_sites(
    list(result.sites[["lat", "lon"]].itertuples(index=False, name=None)),
    labels=site_labels,
    radius=3,
)
plot_depots(depots, fmap=fmap, names=depot_names)
plot_routes(
    [row.polyline for row in visible],
    fmap=fmap,
    colors=[row.colour for row in visible],
    labels=[row.label for row in visible],
)
st_folium(fmap, height=560, use_container_width=True, returned_objects=[])

# ---------------------------------------------------------------- таблица
st.subheader("Маршруты по машинам")

table = pd.DataFrame(
    [
        {
            "": "",  # плашка цвета, связывает строку с линией на карте
            "База": r.depot,
            "Кластер": r.cluster,
            "Машина": r.vehicle,
            "Точек": r.stops,
            "Пробег, км": r.distance_km,
            "Время, мин": r.duration_min,
            "Загрузка": r.load,
            "Загрузка, %": round(100 * r.load / max(int(capacity), 1)),
        }
        for r in rows
    ]
)


def paint(frame: pd.DataFrame) -> pd.DataFrame:
    styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
    for i, row in enumerate(rows):
        colour = CSS_COLOR.get(row.colour, row.colour)
        styles.iloc[i, 0] = f"background-color: {colour}"
    return styles


st.dataframe(
    table.style.apply(paint, axis=None).format({"Пробег, км": "{:.2f}"}),
    width="stretch",
    hide_index=True,
)
st.caption(
    "Цвет плашки совпадает с цветом маршрута на карте. "
    "Чтобы посмотреть один маршрут отдельно, выберите его в списке над картой."
)
st.download_button(
    "Скачать csv",
    table.drop(columns=[""]).to_csv(index=False).encode("utf-8-sig"),
    "routes.csv",
    "text/csv",
)
