"""Интерактивная демонстрация routeforge.

    streamlit run app/streamlit_app.py

Приложение намеренно небольшое: показать пайплайн и дать покрутить
параметры. Вся логика живёт в пакете, здесь только интерфейс.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from routeforge.config import Settings  # noqa: E402
from routeforge.io import ValidationError, read_points  # noqa: E402
from routeforge.pipeline import plan_routes_sync  # noqa: E402
from routeforge.polylines import straight_polylines  # noqa: E402
from routeforge.viz import plot_depots, plot_routes, plot_sites  # noqa: E402

st.set_page_config(page_title="routeforge", page_icon="🚚", layout="wide")
st.title("routeforge — планирование объезда точек")


@st.cache_data
def load_sample() -> tuple[pd.DataFrame, pd.DataFrame]:
    sites = read_points(ROOT / "data/sample/sites.csv")
    depots = pd.read_csv(ROOT / "data/sample/depots.csv")
    return sites, depots


with st.sidebar:
    st.header("Данные")
    uploaded = st.file_uploader("Точки (csv или xlsx)", type=["csv", "xlsx"])

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
    max_per_cluster = st.slider("Точек в кластере", 20, 500, 60, step=10)
    time_limit = st.slider("Секунд солверу на кластер", 1, 60, 5)

    run = st.button("Рассчитать", type="primary", use_container_width=True)

try:
    if uploaded is not None:
        sites = read_points(uploaded if uploaded.name.endswith(".csv") else uploaded)
        depots_df = load_sample()[1]
        st.info("Загружены ваши точки; базы взяты из демо-набора.")
    else:
        sites, depots_df = load_sample()
except (ValidationError, FileNotFoundError) as exc:
    st.error(str(exc))
    st.stop()

depots = list(depots_df[["lat", "lon"]].itertuples(index=False, name=None))

left, right = st.columns([2, 1])
with right:
    st.metric("Точек", len(sites))
    st.metric("Баз", len(depots))
    st.metric("Суммарный спрос", f"{int(sites['demand'].sum()):,}".replace(",", " "))

if not run:
    with left:
        st.subheader("Исходные данные")
        fmap = plot_sites(list(sites[["lat", "lon"]].itertuples(index=False, name=None)))
        plot_depots(depots, fmap=fmap, names=depots_df.get("name"))
        st_folium(fmap, height=520, use_container_width=True, returned_objects=[])
    st.stop()

settings = Settings(
    distance_method=method,
    osrm_url=osrm_url,
    max_sites_per_cluster=max_per_cluster,
    vehicle_capacity=int(capacity),
    vehicle_max_time_min=int(shift),
    vehicle_speed_kmh=float(speed),
    service_time_min=int(service),
    solver_time_limit_s=int(time_limit),
)

with st.spinner("Считаем маршруты…"):
    try:
        result = plan_routes_sync(
            sites, depots, settings=settings, depot_capacities=list(depots_df["capacity"])
        )
    except RuntimeError as exc:
        st.error(f"Не удалось получить расстояния: {exc}")
        st.stop()

lines: list[list[tuple[float, float]]] = []
for (depot_id, cluster_id), solution in sorted(result.solutions.items()):
    chunk = result.sites[
        (result.sites["depot"] == depot_id) & (result.sites["cluster"] == cluster_id)
    ]
    nodes = [depots[depot_id], *chunk[["lat", "lon"]].itertuples(index=False, name=None)]
    lines.extend(straight_polylines([r.nodes for r in solution.routes], nodes))

with right:
    st.metric("Машин", result.vehicles_used)
    st.metric("Общий пробег", f"{result.total_distance_m / 1000:,.0f} км".replace(",", " "))
    if result.unassigned:
        st.warning(f"Не распределено точек: {len(result.unassigned)} — не хватило мощности баз.")

with left:
    st.subheader("Маршруты")
    fmap = plot_sites(
        list(result.sites[["lat", "lon"]].itertuples(index=False, name=None)),
        labels=result.sites["depot"].tolist(),
    )
    plot_depots(depots, fmap=fmap, names=depots_df.get("name"))
    plot_routes(lines, fmap=fmap)
    st_folium(fmap, height=520, use_container_width=True, returned_objects=[])

st.subheader("Маршруты по машинам")
table = result.routes_table()
st.dataframe(table, use_container_width=True, hide_index=True)
st.download_button(
    "Скачать csv", table.to_csv(index=False).encode("utf-8"), "routes.csv", "text/csv"
)
