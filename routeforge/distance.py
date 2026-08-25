"""Матрицы расстояний между точками.

Поддерживаются два источника:

``haversine``
    Расстояние по большому кругу. Работает офлайн, не требует ничего,
    кроме координат. Систематически занижает реальный пробег, но годится
    для быстрой прикидки и для кластеризации, где важен порядок близости,
    а не абсолютное значение.

``osrm``
    Реальные дорожные расстояния из OSRM. Требует поднятого сервера,
    см. ``docs/osrm.md``.

Во всём пакете координаты передаются как ``(lat, lon)``. Порядок
переворачивается ровно в одном месте — при сборке URL для OSRM, который
ожидает ``lon,lat``.
"""

from __future__ import annotations

import asyncio
from math import asin, cos, radians, sin, sqrt
from typing import Iterable, Sequence

import aiohttp
import numpy as np

Coord = tuple[float, float]

EARTH_RADIUS_M = 6_371_000.0

#: Максимум одновременных запросов к OSRM. Сервер обрабатывает запросы
#: пулом потоков, и заваливать его тысячами соединений контрпродуктивно:
#: растёт latency, а пропускная способность не увеличивается.
DEFAULT_CONCURRENCY = 32

#: Сколько точек назначения класть в один запрос ``/table``. OSRM ограничивает
#: размер таблицы (``max-table-size``, по умолчанию 100), поэтому длинные
#: списки режутся на куски.
DEFAULT_TABLE_CHUNK = 90


def haversine_distance(a: Coord, b: Coord) -> float:
    """Расстояние между двумя точками по большому кругу, в метрах."""
    lat1, lon1 = radians(a[0]), radians(a[1])
    lat2, lon2 = radians(b[0]), radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(h))


def haversine_matrix(origins: Sequence[Coord], destinations: Sequence[Coord]) -> np.ndarray:
    """Матрица расстояний ``len(origins) x len(destinations)`` в метрах.

    Считается векторно, поэтому на нескольких тысячах точек отрабатывает
    за доли секунды.
    """
    if not len(origins) or not len(destinations):
        return np.zeros((len(origins), len(destinations)), dtype=np.int64)

    o = np.radians(np.asarray(origins, dtype=float))
    d = np.radians(np.asarray(destinations, dtype=float))

    dlat = d[None, :, 0] - o[:, None, 0]
    dlon = d[None, :, 1] - o[:, None, 1]
    h = (
        np.sin(dlat / 2) ** 2
        + np.cos(o[:, None, 0]) * np.cos(d[None, :, 0]) * np.sin(dlon / 2) ** 2
    )
    return np.rint(2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(h))).astype(np.int64)


def _osrm_coords(coords: Iterable[Coord]) -> str:
    """Собирает координаты в формат OSRM: ``lon,lat;lon,lat;...``."""
    return ";".join(f"{lon},{lat}" for lat, lon in coords)


async def _fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    retries: int = 3,
    delay: float = 1.0,
) -> dict:
    """GET с повторами. Сетевые сбои под нагрузкой — норма, а не исключение."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = exc
            if attempt < retries - 1:
                await asyncio.sleep(delay * (attempt + 1))
    raise RuntimeError(f"OSRM не ответил после {retries} попыток: {url}") from last_error


async def osrm_matrix(
    origins: Sequence[Coord],
    destinations: Sequence[Coord],
    osrm_url: str,
    *,
    chunk_size: int = DEFAULT_TABLE_CHUNK,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = 300.0,
) -> np.ndarray:
    """Матрица дорожных расстояний через OSRM ``/table``, в метрах.

    Используется ``/table``, а не ``/route`` по каждой паре: один запрос
    возвращает целую строку матрицы, поэтому запросов получается
    ``len(origins) * ceil(len(destinations) / chunk_size)`` вместо
    ``len(origins) * len(destinations)``. На задаче 7 приёмников x 1000
    площадок это 84 запроса против 7000.
    """
    n_o, n_d = len(origins), len(destinations)
    if not n_o or not n_d:
        return np.zeros((n_o, n_d), dtype=np.int64)

    matrix = np.zeros((n_o, n_d), dtype=np.int64)
    base = osrm_url.rstrip("/")
    semaphore = asyncio.Semaphore(concurrency)

    async def one_chunk(session: aiohttp.ClientSession, i: int, start: int, stop: int) -> None:
        chunk = destinations[start:stop]
        coords = _osrm_coords([origins[i], *chunk])
        # sources=0 — считаем только от origin до каждой точки чанка,
        # иначе OSRM вернёт полную квадратную матрицу и потратит время зря.
        url = f"{base}/table/v1/driving/{coords}?annotations=distance&sources=0"
        async with semaphore:
            payload = await _fetch_json(session, url)
        row = payload["distances"][0][1:]
        matrix[i, start:stop] = [int(round(v)) if v is not None else 0 for v in row]

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        await asyncio.gather(
            *(
                one_chunk(session, i, start, min(start + chunk_size, n_d))
                for i in range(n_o)
                for start in range(0, n_d, chunk_size)
            )
        )
    return matrix


async def distance_matrix(
    origins: Sequence[Coord],
    destinations: Sequence[Coord],
    method: str = "haversine",
    osrm_url: str | None = None,
    **kwargs,
) -> np.ndarray:
    """Единая точка входа: считает матрицу выбранным методом.

    :param method: ``"haversine"`` или ``"osrm"``.
    :param osrm_url: обязателен при ``method="osrm"``.
    """
    method = method.lower()
    if method == "haversine":
        return haversine_matrix(origins, destinations)
    if method == "osrm":
        if not osrm_url:
            raise ValueError("Для method='osrm' нужен osrm_url")
        return await osrm_matrix(origins, destinations, osrm_url, **kwargs)
    raise ValueError(f"Неизвестный метод {method!r}. Ожидается 'haversine' или 'osrm'.")
