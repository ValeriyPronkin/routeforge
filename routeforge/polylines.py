"""Геометрия маршрутов для отрисовки на карте.

Маршрут, полученный от солвера, — это последовательность индексов точек.
Чтобы показать его на карте, нужна ломаная. Есть два варианта:

* прямые отрезки между точками — бесплатно, но по карте едет «по воздуху»;
* реальная геометрия дороги из OSRM — то, что видит водитель.

Обе функции возвращают **один и тот же тип**: по одному списку координат
``(lat, lon)`` на маршрут. В исходной версии проекта эти две ветки
возвращали разные структуры (список точек на маршрут против плоского
списка закодированных строк на сегмент), и слой визуализации вынужден был
разбираться, что ему пришло.
"""

from __future__ import annotations

import asyncio
from typing import Mapping, Sequence

import aiohttp
import polyline as polyline_codec

from .distance import (
    DEFAULT_CONCURRENCY,
    Coord,
    _fetch_json,
    _osrm_coords,
)

Route = Sequence[int]


def straight_polylines(
    routes: Sequence[Route],
    locations: Mapping[int, Coord] | Sequence[Coord],
) -> list[list[Coord]]:
    """Ломаные из прямых отрезков — по одной на маршрут."""
    result: list[list[Coord]] = []
    for route in routes:
        result.append([tuple(locations[i]) for i in route])
    return result


async def osrm_polylines(
    routes: Sequence[Route],
    locations: Mapping[int, Coord] | Sequence[Coord],
    osrm_url: str,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = 300.0,
) -> list[list[Coord]]:
    """Реальная геометрия маршрутов из OSRM — по одной ломаной на маршрут.

    Весь маршрут запрашивается одним обращением к ``/route`` со всеми
    промежуточными точками, а не по отрезку на пару соседних точек:
    OSRM всё равно строит непрерывный путь, а запросов выходит на порядок
    меньше.
    """
    base = osrm_url.rstrip("/")
    semaphore = asyncio.Semaphore(concurrency)

    async def one_route(session: aiohttp.ClientSession, route: Route) -> list[Coord]:
        points = [tuple(locations[i]) for i in route]
        if len(points) < 2:
            return points
        url = (
            f"{base}/route/v1/driving/{_osrm_coords(points)}"
            "?overview=full&geometries=polyline"
        )
        async with semaphore:
            payload = await _fetch_json(session, url)
        routes_found = payload.get("routes") or []
        if not routes_found:
            # OSRM не смог связать точки дорогой — падать на этом не стоит,
            # показываем хотя бы прямые отрезки.
            return points
        return [tuple(p) for p in polyline_codec.decode(routes_found[0]["geometry"])]

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        return list(await asyncio.gather(*(one_route(session, r) for r in routes)))


async def route_polylines(
    routes: Sequence[Route],
    locations: Mapping[int, Coord] | Sequence[Coord],
    method: str = "haversine",
    osrm_url: str | None = None,
    **kwargs,
) -> list[list[Coord]]:
    """Единая точка входа, симметричная :func:`routeforge.distance.distance_matrix`."""
    method = method.lower()
    if method == "haversine":
        return straight_polylines(routes, locations)
    if method == "osrm":
        if not osrm_url:
            raise ValueError("Для method='osrm' нужен osrm_url")
        return await osrm_polylines(routes, locations, osrm_url, **kwargs)
    raise ValueError(f"Неизвестный метод {method!r}. Ожидается 'haversine' или 'osrm'.")
