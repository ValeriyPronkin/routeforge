"""Готовый пайплайн: от таблицы точек до маршрутов.

Собирает шаги из остальных модулей в один вызов. Если нужен контроль над
отдельным этапом — берите модули напрямую, здесь только типовой сценарий.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
from loguru import logger

from .clustering import UNASSIGNED, assign_to_depots, optimal_k_by_cluster_size
from .config import Settings
from .distance import Coord, distance_matrix
from .fleet import Vehicle, VehiclePool
from .solver import Fleet, Solution, SolverConfig, estimate_vehicles, solve_cvrp


@dataclass
class PlanResult:
    """Результат планирования по всем базам и кластерам."""

    #: Исходные точки с колонками ``depot`` и ``cluster``.
    sites: pd.DataFrame
    #: Решения по ключу ``(depot, cluster)``.
    solutions: dict[tuple[int, int], Solution] = field(default_factory=dict)
    #: Точки, которые не приняла ни одна база: не хватило мощности.
    unassigned: list[int] = field(default_factory=list)
    #: Точки, которые бросил солвер: не хватило машин или времени смены.
    #: Это отдельный вид потерь, и путать его с предыдущим нельзя — лечится
    #: он другим: парком и длиной смены, а не мощностью баз.
    dropped: list[int] = field(default_factory=list)
    #: Номера машин по ключу ``(depot, cluster)``, в порядке индексов внутри
    #: решения: ``vehicle_ids[key][route.vehicle]`` — номер этой машины.
    #: Пусто, когда расчёт шёл без реестра и машины безымянны.
    vehicle_ids: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    #: Реестр машин, как он выглядит после расчёта: у каждой машины виден
    #: остаток смены. Копия переданного в ``plan_routes`` — исходный список
    #: расчёт не трогает, иначе второй запуск начинался бы с исчерпанным
    #: парком. Пусто, когда реестра не было.
    vehicles: list[Vehicle] = field(default_factory=list)

    @property
    def unserved(self) -> list[int]:
        """Все точки, не попавшие ни в один маршрут."""
        return sorted({*self.unassigned, *self.dropped})

    @property
    def total_distance_m(self) -> int:
        return sum(s.total_distance_m for s in self.solutions.values())

    @property
    def vehicles_used(self) -> int:
        return sum(s.vehicles_used for s in self.solutions.values())

    def vehicles_table(self) -> pd.DataFrame:
        """Сводка по машинам: чем занят парк.

        Строка на каждую машину реестра, включая те, что никуда не поехали:
        простаивающая машина — такой же результат расчёта, как загруженная, и
        видеть её нужно. Без реестра сводка строится по номерам из решений, и
        тогда в ней только те, кто работал.

        Колонки: число рейсов и точек, пробег, вывезенный объём (в единицах
        спроса), отработанное время, доля смены и её остаток.
        """
        routes = self.routes_table()
        worked = (
            routes[routes["vehicle_id"] != ""]
            .groupby("vehicle_id")
            .agg(
                routes_count=("vehicle_id", "size"),
                stops=("stops", "sum"),
                distance_km=("distance_km", "sum"),
                load=("load", "sum"),
                duration_min=("duration_min", "sum"),
            )
        )

        known = {v.id: v for v in self.vehicles}
        order = list(known) or list(worked.index)
        rows = []
        for number in order:
            row = worked.loc[number] if number in worked.index else None
            vehicle = known.get(number)
            duration = int(row["duration_min"]) if row is not None else 0
            shift = vehicle.max_time_min if vehicle else duration
            rows.append(
                {
                    "vehicle_id": number,
                    "routes": int(row["routes_count"]) if row is not None else 0,
                    "stops": int(row["stops"]) if row is not None else 0,
                    "distance_km": round(float(row["distance_km"]), 2) if row is not None else 0.0,
                    "load": int(row["load"]) if row is not None else 0,
                    "duration_min": duration,
                    "shift_used_pct": round(100 * duration / shift) if shift else 0,
                    # Остаток берётся у машины, а не считается вычитанием:
                    # ниже порога он обнулён, и это осмысленный ноль — на
                    # линию с таким остатком не выезжают.
                    "remaining_min": vehicle.remaining_min if vehicle else 0,
                }
            )
        return pd.DataFrame(rows)

    def routes_table(self) -> pd.DataFrame:
        """Плоская таблица маршрутов — то, что уходит в отчёт."""
        rows = []
        for (depot, cluster), solution in sorted(self.solutions.items()):
            numbers = self.vehicle_ids.get((depot, cluster), [])
            for route in solution.routes:
                rows.append(
                    {
                        "depot": depot,
                        "cluster": cluster,
                        "vehicle": route.vehicle,
                        # Номер из реестра. Без него в отчёте остаётся индекс,
                        # по которому конкретную машину уже не найти.
                        "vehicle_id": (
                            numbers[route.vehicle] if route.vehicle < len(numbers) else ""
                        ),
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
    vehicles: list[Vehicle] | None = None,
) -> PlanResult:
    """Планирует объезд: базы -> кластеры -> маршруты.

    :param sites: точки с колонками ``lat``, ``lon``, ``demand``
        (см. :func:`routeforge.io.read_points`).
    :param depots: координаты баз.
    :param depot_capacities: мощность баз; ``None`` — не ограничивать.
    :param fleet: парк машин; ``None`` — собрать из ``settings``, а число
        машин на каждый кластер оценить автоматически.
    :param vehicles: реестр реальных машин. Задан — парк перестаёт быть
        оценкой: машины раздаются по кластерам из общего списка, у каждой
        убывает остаток времени, и одна и та же машина не может оказаться в
        двух кластерах сразу. Не хватило машин — точки уйдут в ``dropped``.
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

    # Копия: расчёт списывает остаток смены, и делать это в списке
    # вызывающего нельзя — второй запуск начался бы с уже уставшим парком.
    own_vehicles = [replace(v) for v in vehicles] if vehicles is not None else None
    result.vehicles = own_vehicles or []
    pool = (
        VehiclePool(
            own_vehicles,
            min_remaining_min=settings.vehicle_min_remaining_min,
            capacity_reserve=settings.fleet_capacity_reserve,
        )
        if own_vehicles is not None
        else None
    )

    # 3. Дробление крупных групп и решение CVRP внутри каждой.
    sites["cluster"] = 0
    for depot_id in sorted(set(sites["depot"]) - {UNASSIGNED}):
        block = sites[sites["depot"] == depot_id]
        coords = np.asarray(list(block[["lat", "lon"]].itertuples(index=False, name=None)))
        _, labels = optimal_k_by_cluster_size(coords, settings.max_sites_per_cluster)
        sites.loc[block.index, "cluster"] = labels

        for cluster_id in _cluster_order(block, labels, by_demand=pool is not None):
            chunk = block[labels == cluster_id]
            key = (int(depot_id), int(cluster_id))

            if pool is None:
                solution = await _solve_chunk(chunk, depots[depot_id], settings, fleet)
            else:
                picked = pool.pick(float(chunk["demand"].sum()), depot=int(depot_id))
                result.vehicle_ids[key] = [v.id for v in picked]
                if not picked:
                    # Свободных машин не осталось. Молчать нельзя: кластер
                    # целиком уходит в потери, и это должно быть видно.
                    logger.warning(
                        "Кластер {}: свободных машин нет, {} точек без маршрута",
                        key, len(chunk),
                    )
                    result.solutions[key] = Solution()
                    result.dropped.extend(int(i) for i in chunk.index)
                    continue
                solution = await _solve_chunk(
                    chunk,
                    depots[depot_id],
                    settings,
                    pool.as_fleet(
                        picked,
                        speed_kmh=settings.vehicle_speed_kmh,
                        service_time_min=settings.service_time_min,
                    ),
                )
                # Отработанное списывается сразу: следующий кластер должен
                # увидеть машину с тем временем, что у неё реально осталось.
                for route in solution.routes:
                    pool.spend(picked[route.vehicle], route.duration_min)

            result.solutions[key] = solution
            # Узлы нумеруются внутри кластера, причём нулевой — это база,
            # поэтому обслуживаемая точка n соответствует chunk.index[n - 1].
            result.dropped.extend(int(chunk.index[n - 1]) for n in solution.dropped)

    result.sites = sites
    return result


def _cluster_order(block: pd.DataFrame, labels: np.ndarray, *, by_demand: bool) -> list[int]:
    """В каком порядке считать кластеры одной базы.

    При работе от реестра — по убыванию спроса: парк конечен, и крупные
    кластеры должны получить машины первыми. Иначе порядок не важен, и
    сортировка по номеру оставляет вывод предсказуемым.
    """
    ids = sorted(set(int(v) for v in labels))
    if not by_demand:
        return ids
    return sorted(ids, key=lambda c: float(block[labels == c]["demand"].sum()), reverse=True)


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

    auto_fleet = fleet is None
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

    config = SolverConfig(
        time_limit_s=settings.solver_time_limit_s,
        drop_penalty=settings.drop_penalty,
    )
    solution = solve_cvrp(matrix, demands, fleet, depot=0, config=config)

    # Оценка числа машин по суммарному спросу бывает впритык: солверу дешевле
    # заплатить штраф и бросить точку, чем нарушить вместимость. Терять точку
    # молча нельзя, поэтому парк добирается по одной машине.
    # При явно заданном парке этого не делаем — раз количество указано, значит
    # оно и есть ограничение задачи.
    if auto_fleet:
        for extra in range(1, settings.auto_add_vehicles + 1):
            if not solution.dropped:
                break
            bigger = replace(fleet, count=fleet.count + extra)
            retry = solve_cvrp(matrix, demands, bigger, depot=0, config=config)
            if len(retry.dropped) < len(solution.dropped):
                logger.info(
                    "Кластер: {} точек брошено при {} машинах, добавили {} -> брошено {}",
                    len(solution.dropped), fleet.count, extra, len(retry.dropped),
                )
                solution = retry
    return solution


def plan_routes_sync(*args, **kwargs) -> PlanResult:
    """Синхронная обёртка для скриптов и ноутбуков."""
    return asyncio.run(plan_routes(*args, **kwargs))
