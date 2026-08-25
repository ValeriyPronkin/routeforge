"""Второй шаг схемы: маршрутизация внутри кластера.

Обёртка над OR-Tools для CVRP с ограничениями по вместимости и по времени
смены. Модуль ничего не знает про pandas, Streamlit и формат входных
файлов — на вход матрица расстояний и спрос, на выходе маршруты.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2


@dataclass(frozen=True)
class Fleet:
    """Парк машин: однородный или разнородный.

    :param count: число машин.
    :param capacity: вместимость одной машины в тех же единицах, что и спрос.
    :param max_time_min: предел смены в минутах.
    :param speed_kmh: средняя скорость — переводит расстояние во время.
    :param service_time_min: время обслуживания одной точки.
    :param capacities: вместимость по машинам. Задаётся, когда парк реальный
        и машины разные; ``None`` — у всех одинаковая ``capacity``.
    :param max_times_min: остаток смены по машинам. Нужен, когда машина уже
        отработала часть дня в другом кластере: у неё меньше времени, чем у
        только что вышедшей.
    """

    count: int
    capacity: int
    max_time_min: int = 8 * 60
    speed_kmh: float = 40.0
    service_time_min: int = 10
    capacities: tuple[int, ...] | None = None
    max_times_min: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        for name in ("capacities", "max_times_min"):
            values = getattr(self, name)
            if values is not None and len(values) != self.count:
                raise ValueError(
                    f"длина {name} ({len(values)}) не совпадает с числом машин ({self.count})"
                )

    @property
    def capacity_per_vehicle(self) -> list[int]:
        if self.capacities is None:
            return [int(self.capacity)] * self.count
        return [int(c) for c in self.capacities]

    @property
    def time_per_vehicle(self) -> list[int]:
        if self.max_times_min is None:
            return [int(self.max_time_min)] * self.count
        return [int(t) for t in self.max_times_min]


@dataclass(frozen=True)
class SolverConfig:
    """Параметры поиска.

    :param time_limit_s: сколько секунд солверу отведено. Значение по
        умолчанию в исходной версии было равно 2 секундам и зашито в код;
        на кластере из сотен точек этого мало, поэтому параметр вынесен.
    :param drop_penalty: штраф за пропуск точки. Должен заметно превышать
        стоимость крюка до самой дальней точки, иначе солверу выгоднее
        бросить её, чем заехать. ``None`` — пропуск запрещён вовсе.
    """

    time_limit_s: int = 30
    drop_penalty: int | None = 1_000_000
    first_solution: str = "PATH_CHEAPEST_ARC"
    metaheuristic: str = "GUIDED_LOCAL_SEARCH"
    log_search: bool = False


@dataclass
class Route:
    """Один маршрут одной машины."""

    vehicle: int
    nodes: list[int]
    distance_m: int
    load: int
    duration_min: int


@dataclass
class Solution:
    """Результат расчёта."""

    routes: list[Route] = field(default_factory=list)
    dropped: list[int] = field(default_factory=list)
    total_distance_m: int = 0

    @property
    def vehicles_used(self) -> int:
        return len(self.routes)


def estimate_vehicles(
    demands: Sequence[float],
    capacity: float,
    distance_matrix: np.ndarray | None = None,
    *,
    max_time_min: int = 8 * 60,
    speed_kmh: float = 40.0,
    service_time_min: int = 10,
) -> int:
    """Сколько машин нужно, чтобы задача вообще имела решение.

    Берётся максимум из двух оценок: по суммарному спросу и вместимости и,
    если дана матрица расстояний, по суммарному времени работы. Занижать
    это число нельзя — солвер просто не найдёт допустимого решения.
    """
    total_demand = float(np.sum(demands))
    by_capacity = int(np.ceil(total_demand / capacity)) if capacity > 0 else 1

    by_time = 1
    if distance_matrix is not None and len(demands):
        matrix = np.asarray(distance_matrix, dtype=float)
        # Грубая оценка пробега: для каждой точки — ближайший сосед.
        finite = np.where(matrix > 0, matrix, np.inf)
        nearest = np.min(finite, axis=1)
        nearest = nearest[np.isfinite(nearest)]
        travel_min = nearest.sum() * 60 / max(speed_kmh, 1e-6) / 1000
        service_min = service_time_min * len(demands)
        by_time = int(np.ceil((travel_min + service_min) / max(max_time_min, 1)))

    return max(1, by_capacity, by_time)


_FIRST_SOLUTION = {
    "PATH_CHEAPEST_ARC": routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
    "SAVINGS": routing_enums_pb2.FirstSolutionStrategy.SAVINGS,
    "PARALLEL_CHEAPEST_INSERTION": routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION,
    "CHRISTOFIDES": routing_enums_pb2.FirstSolutionStrategy.CHRISTOFIDES,
}

_METAHEURISTIC = {
    "GUIDED_LOCAL_SEARCH": routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH,
    "TABU_SEARCH": routing_enums_pb2.LocalSearchMetaheuristic.TABU_SEARCH,
    "SIMULATED_ANNEALING": routing_enums_pb2.LocalSearchMetaheuristic.SIMULATED_ANNEALING,
    "AUTOMATIC": routing_enums_pb2.LocalSearchMetaheuristic.AUTOMATIC,
}


def solve_cvrp(
    distance_matrix: np.ndarray,
    demands: Sequence[int],
    fleet: Fleet,
    *,
    depot: int = 0,
    starts: Sequence[int] | None = None,
    ends: Sequence[int] | None = None,
    config: SolverConfig | None = None,
) -> Solution:
    """Решает CVRP и возвращает маршруты.

    :param distance_matrix: квадратная матрица расстояний в метрах, целые числа.
    :param demands: спрос каждого узла; у депо должен быть ноль.
    :param depot: индекс депо, если все машины стартуют и финишируют в нём.
    :param starts, ends: точки старта и финиша по машинам — для случая,
        когда выезд и возврат разнесены (например, гараж и склад).
        Задаются вместе и вместо ``depot``.
    """
    config = config or SolverConfig()
    matrix = np.asarray(distance_matrix)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("distance_matrix должна быть квадратной")
    n_nodes = matrix.shape[0]
    if len(demands) != n_nodes:
        raise ValueError("demands должен быть длины n_nodes")
    if (starts is None) != (ends is None):
        raise ValueError("starts и ends задаются только вместе")

    matrix = np.rint(matrix).astype(np.int64)
    demands = [int(d) for d in demands]

    if starts is None:
        manager = pywrapcp.RoutingIndexManager(n_nodes, fleet.count, depot)
        terminal_nodes = {depot}
    else:
        if len(starts) != fleet.count or len(ends) != fleet.count:
            raise ValueError("длины starts и ends должны совпадать с fleet.count")
        manager = pywrapcp.RoutingIndexManager(n_nodes, fleet.count, list(starts), list(ends))
        terminal_nodes = set(starts) | set(ends)

    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index: int, to_index: int) -> int:
        return int(matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)])

    transit_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_index)

    def time_callback(from_index: int, to_index: int) -> int:
        metres = matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
        travel_min = metres * 60 / fleet.speed_kmh / 1000
        return int(travel_min + fleet.service_time_min)

    time_index = routing.RegisterTransitCallback(time_callback)
    times = fleet.time_per_vehicle
    # Горизонт измерения общий, а предел у каждой машины свой: у той, что уже
    # отработала часть дня, времени меньше. Одним AddDimension этого не
    # выразить, поэтому потолок ставится машине отдельно, на конце маршрута.
    routing.AddDimension(time_index, 0, max(times) if times else 0, True, "Time")
    time_dimension = routing.GetDimensionOrDie("Time")
    for vehicle, limit in enumerate(times):
        time_dimension.CumulVar(routing.End(vehicle)).SetMax(limit)

    def demand_callback(from_index: int) -> int:
        return demands[manager.IndexToNode(from_index)]

    demand_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_index, 0, fleet.capacity_per_vehicle, True, "Capacity"
    )

    if config.drop_penalty is not None:
        # Пропускать можно только обслуживаемые точки, но не депо. В исходной
        # версии здесь стояло range(2, n) — жёстко зашитое предположение, что
        # депо это ровно узлы 0 и 1.
        for node in range(n_nodes):
            if node not in terminal_nodes:
                routing.AddDisjunction([manager.NodeToIndex(node)], config.drop_penalty)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = _FIRST_SOLUTION[config.first_solution]
    params.local_search_metaheuristic = _METAHEURISTIC[config.metaheuristic]
    params.time_limit.FromSeconds(config.time_limit_s)
    params.log_search = config.log_search

    assignment = routing.SolveWithParameters(params)
    if assignment is None:
        return Solution()
    return _extract(manager, routing, assignment, fleet, demands, n_nodes, terminal_nodes)


def _extract(manager, routing, assignment, fleet, demands, n_nodes, terminal_nodes) -> Solution:
    """Разбирает решение OR-Tools в :class:`Solution`."""
    time_dim = routing.GetDimensionOrDie("Time")
    solution = Solution()
    visited: set[int] = set()

    for vehicle in range(fleet.count):
        index = routing.Start(vehicle)
        if not routing.IsVehicleUsed(assignment, vehicle):
            continue
        nodes: list[int] = []
        distance = 0
        load = 0
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            nodes.append(node)
            visited.add(node)
            load += demands[node]
            previous = index
            index = assignment.Value(routing.NextVar(index))
            distance += routing.GetArcCostForVehicle(previous, index, vehicle)
        end_node = manager.IndexToNode(index)
        nodes.append(end_node)
        visited.add(end_node)
        duration = assignment.Value(time_dim.CumulVar(index))
        solution.routes.append(
            Route(vehicle=vehicle, nodes=nodes, distance_m=int(distance), load=int(load), duration_min=int(duration))
        )
        solution.total_distance_m += int(distance)

    solution.dropped = [n for n in range(n_nodes) if n not in visited and n not in terminal_nodes]
    return solution
