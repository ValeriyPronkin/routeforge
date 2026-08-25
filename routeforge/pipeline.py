"""Готовый пайплайн: от таблицы точек до маршрутов.

Собирает шаги из остальных модулей в один вызов. Если нужен контроль над
отдельным этапом — берите модули напрямую, здесь только типовой сценарий.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .clustering import UNASSIGNED, assign_to_depots, optimal_k_by_cluster_size
from .config import Settings
from .distance import Coord, distance_matrix
from .solver import Fleet, Solution, SolverConfig, estimate_vehicles, solve_cvrp


@dataclass
class PlanResult:
    """Результат планирования по всем базам и кластерам."""

    #: Исходные точки с колонками ``depot`` и ``cluster``.
    sites: pd.DataFrame
    #: Решения по ключу ``(depot, cluster)``.
    solutions: dict[tuple[int, int], Solution] = field(default_factory=dict)
    #: Точки, которые не приняла ни одна база.
    unassigned: list[int] = field(default_factory=list)

    @property
    def total_distance_m(self) -> int:
        return sum(s.total_distance_m for s in self.solutions.values())

    @property
    def vehicles_used(self) -> int:
        return sum(s.vehicles_used for s in self.solutions.values())

    def routes_table(self) -> pd.DataFrame:
        """Плоская таблица маршрутов — то, что уходит в отчёт."""
        rows = []
        for (depot, cluster), solution in sorted(self.solutions.items()):
            for route in solution.routes:
                rows.append(
                    {
                        "depot": depot,
                        "cluster": cluster,
                        "vehicle": route.vehicle,
                        "stops": max(0, len(route.nodes) - 2),
                        "distance_km": round(route.distance_m / 1000, 2),
                        "duration_min": route.duration_min,
                        "load": route.load,
                    }
                )
        return pd.DataFrame(rows)


async def plan_routes(
    sites: pd.DataFrame,
    depots: list[Coord],
    *,
    settings: Settings | None = None,
    depot_capacities: list[float] | None = None,
    fleet: Fleet | None = None,
) -> PlanResult:
    """Планирует объезд: базы -> кластеры -> маршруты.

    :param sites: точки с колонками ``lat``, ``lon``, ``demand``
        (см. :func:`routeforge.io.read_points`).
    :param depots: координаты баз.
    :param depot_capacities: мощность баз; ``None`` — не ограничивать.
    :param fleet: парк машин; ``None`` — собрать из ``settings``, а число
        машин на каждый кластер оценить автоматически.
    """
    settings = settings or Settings()
    sites = sites.reset_index(drop=True).copy()
    site_coords: list[Coord] = list(sites[["lat", "lon"]].itertuples(index=False, name=None))

    # 1. Расстояния от каждой точки до каждой базы.
    to_depots = await distance_matrix(
        site_coords, depots, method=settings.distance_method, osrm_url=settings.osrm_url
    )
    # distance_matrix отдаёт origins x destinations; для assign_to_depots
    # нужна ориентация sites x depots — она уже такая.

    # 2. Распределение по базам.
    sites["depot"] = assign_to_depots(
        to_depots,
        demands=sites["demand"].to_numpy(),
        capacities=np.asarray(depot_capacities, dtype=float) if depot_capacities else None,
    )
    result = PlanResult(sites=sites)
    result.unassigned = sites.index[sites["depot"] == UNASSIGNED].tolist()

    # 3. Дробление крупных групп и решение CVRP внутри каждой.
    sites["cluster"] = 0
    for depot_id in sorted(set(sites["depot"]) - {UNASSIGNED}):
        block = sites[sites["depot"] == depot_id]
        coords = np.asarray(list(block[["lat", "lon"]].itertuples(index=False, name=None)))
        _, labels = optimal_k_by_cluster_size(coords, settings.max_sites_per_cluster)
        sites.loc[block.index, "cluster"] = labels

        for cluster_id in sorted(set(labels)):
            chunk = block[labels == cluster_id]
            solution = await _solve_chunk(chunk, depots[depot_id], settings, fleet)
            result.solutions[(int(depot_id), int(cluster_id))] = solution

    result.sites = sites
    return result


async def _solve_chunk(
    chunk: pd.DataFrame,
    depot: Coord,
    settings: Settings,
    fleet: Fleet | None,
) -> Solution:
    """Решает CVRP для одного кластера. Узел 0 — база."""
    points: list[Coord] = [depot, *chunk[["lat", "lon"]].itertuples(index=False, name=None)]
    matrix = await distance_matrix(
        points, points, method=settings.distance_method, osrm_url=settings.osrm_url
    )
    demands = [0, *(int(round(d)) for d in chunk["demand"])]

    if fleet is None:
        count = estimate_vehicles(
            demands[1:],
            settings.vehicle_capacity,
            matrix,
            max_time_min=settings.vehicle_max_time_min,
            speed_kmh=settings.vehicle_speed_kmh,
            service_time_min=settings.service_time_min,
        )
        fleet = Fleet(
            count=count,
            capacity=settings.vehicle_capacity,
            max_time_min=settings.vehicle_max_time_min,
            speed_kmh=settings.vehicle_speed_kmh,
            service_time_min=settings.service_time_min,
        )

    return solve_cvrp(
        matrix,
        demands,
        fleet,
        depot=0,
        config=SolverConfig(time_limit_s=settings.solver_time_limit_s),
    )


def plan_routes_sync(*args, **kwargs) -> PlanResult:
    """Синхронная обёртка для скриптов и ноутбуков."""
    return asyncio.run(plan_routes(*args, **kwargs))
