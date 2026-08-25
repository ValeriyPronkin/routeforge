"""Отрисовка точек, кластеров и маршрутов на карте folium."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

import folium
import numpy as np

from .distance import Coord

#: Палитра для кластеров и маршрутов. Только имена, которые folium
#: действительно понимает: в исходном проекте список из 130 цветов содержал
#: опечатки ('mediumturquose', 'darkturqueise', 'darkgoldenrot', и даже
#: 'honeydew:свежего меда'), из-за чего маркер молча уезжал в дефолтный цвет.
PALETTE: tuple[str, ...] = (
    "red", "blue", "green", "purple", "orange", "darkred", "cadetblue",
    "darkblue", "darkgreen", "black", "pink", "lightblue", "lightgreen",
    "gray", "beige", "darkpurple",
)


def color_for(index: int) -> str:
    """Цвет по номеру кластера — с циклическим переиспользованием."""
    return PALETTE[int(index) % len(PALETTE)]


def _centre(points: Sequence[Coord]) -> Coord:
    arr = np.asarray(points, dtype=float)
    return float(arr[:, 0].mean()), float(arr[:, 1].mean())


def base_map(points: Sequence[Coord], zoom_start: int = 11) -> folium.Map:
    """Пустая карта, отцентрованная по облаку точек."""
    if not len(points):
        return folium.Map(location=(55.75, 37.62), zoom_start=zoom_start)
    return folium.Map(location=_centre(points), zoom_start=zoom_start)


def plot_sites(
    points: Sequence[Coord],
    labels: Sequence[int] | None = None,
    *,
    popups: Sequence[str] | None = None,
    fmap: folium.Map | None = None,
    radius: int = 4,
) -> folium.Map:
    """Точки обслуживания. ``labels`` раскрашивает их по кластерам."""
    fmap = fmap or base_map(points)
    for i, point in enumerate(points):
        folium.CircleMarker(
            location=tuple(point),
            radius=radius,
            color=color_for(labels[i]) if labels is not None else "blue",
            fill=True,
            fill_opacity=0.8,
            popup=popups[i] if popups is not None else None,
        ).add_to(fmap)
    return fmap


def plot_depots(
    depots: Sequence[Coord],
    *,
    fmap: folium.Map | None = None,
    names: Sequence[str] | None = None,
) -> folium.Map:
    """Базы — отдельным значком, чтобы не путались с точками обслуживания."""
    fmap = fmap or base_map(depots)
    for i, depot in enumerate(depots):
        folium.Marker(
            location=tuple(depot),
            popup=names[i] if names is not None else f"Депо {i}",
            icon=folium.Icon(color=color_for(i), icon="home", prefix="fa"),
        ).add_to(fmap)
    return fmap


def plot_routes(
    polylines: Sequence[Sequence[Coord]],
    *,
    fmap: folium.Map | None = None,
    weight: int = 3,
    opacity: float = 0.8,
) -> folium.Map:
    """Маршруты поверх карты. Ожидает результат
    :func:`routeforge.polylines.route_polylines` — по ломаной на маршрут."""
    flat = [p for line in polylines for p in line]
    fmap = fmap or base_map(flat)
    for i, line in enumerate(polylines):
        if not line or len(line) < 2:
            continue
        folium.PolyLine(
            locations=[tuple(p) for p in line],
            color=color_for(i),
            weight=weight,
            opacity=opacity,
            popup=f"Маршрут {i}",
        ).add_to(fmap)
    return fmap


def save_map(fmap: folium.Map, path: str | Path) -> Path:
    """Сохраняет карту в html и возвращает путь."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(str(path))
    return path
