"""routeforge — планировщик объезда точек по схеме «cluster-first, route-second».

Задача: развезти или собрать по множеству точек, имея несколько баз и парк
машин с ограниченной вместимостью и длительностью смены. Возникает при
объезде контейнерных площадок, доставке по магазинам, обслуживании
оборудования — предметная область меняется, постановка нет.

Пайплайн:

1. :mod:`routeforge.io` — прочитать точки, проверить координаты;
2. :mod:`routeforge.geocoding` — при необходимости получить координаты по адресам;
3. :mod:`routeforge.distance` — матрица расстояний, по прямой или по дорогам через OSRM;
4. :mod:`routeforge.clustering` — распределить точки по базам и раздробить крупные группы;
5. :mod:`routeforge.solver` — решить CVRP внутри каждой группы;
6. :mod:`routeforge.polylines` и :mod:`routeforge.viz` — показать результат на карте.
"""

from .config import Settings
from .distance import distance_matrix, haversine_distance, haversine_matrix, osrm_matrix
from .clustering import assign_to_depots, balanced_clusters, optimal_k_by_cluster_size
from .polylines import route_polylines
from .solver import Fleet, Route, Solution, SolverConfig, estimate_vehicles, solve_cvrp

__version__ = "0.1.0"

__all__ = [
    "Settings",
    "distance_matrix",
    "haversine_distance",
    "haversine_matrix",
    "osrm_matrix",
    "assign_to_depots",
    "balanced_clusters",
    "optimal_k_by_cluster_size",
    "route_polylines",
    "Fleet",
    "Route",
    "Solution",
    "SolverConfig",
    "estimate_vehicles",
    "solve_cvrp",
]
