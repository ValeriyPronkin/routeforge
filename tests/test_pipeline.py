import pandas as pd
import pytest

from routeforge.clustering import UNASSIGNED
from routeforge.config import Settings
from routeforge.pipeline import plan_routes
from routeforge.solver import Fleet

FAST = Settings(distance_method="haversine", max_sites_per_cluster=20, solver_time_limit_s=2)


@pytest.fixture
def sites():
    # Два плотных облака вокруг двух баз.
    rows = []
    for base_lat, base_lon in ((55.30, 61.50), (55.05, 61.30)):
        for i in range(12):
            rows.append(
                {
                    "id": f"{base_lat}-{i}",
                    "lat": base_lat + 0.01 * (i % 4),
                    "lon": base_lon + 0.01 * (i // 4),
                    "demand": 100,
                }
            )
    return pd.DataFrame(rows)


DEPOTS = [(55.30, 61.50), (55.05, 61.30)]


async def test_every_site_is_planned(sites):
    result = await plan_routes(sites, DEPOTS, settings=FAST)
    assert result.unassigned == []
    assert result.vehicles_used >= 1
    assert result.total_distance_m > 0


async def test_sites_go_to_the_nearer_depot(sites):
    result = await plan_routes(sites, DEPOTS, settings=FAST)
    north = result.sites[result.sites["lat"] > 55.2]
    south = result.sites[result.sites["lat"] < 55.2]
    assert set(north["depot"]) == {0}
    assert set(south["depot"]) == {1}


async def test_depot_capacity_leaves_points_unassigned(sites):
    # Суммарный спрос 2400, мощности хватает меньше чем на половину.
    result = await plan_routes(sites, DEPOTS, settings=FAST, depot_capacities=[500, 500])
    assert result.unassigned
    assert (result.sites["depot"] == UNASSIGNED).sum() == len(result.unassigned)


async def test_routes_table_matches_solutions(sites):
    result = await plan_routes(sites, DEPOTS, settings=FAST)
    table = result.routes_table()
    assert len(table) == result.vehicles_used
    assert set(table.columns) == {
        "depot", "cluster", "vehicle", "stops", "distance_km", "duration_min", "load",
    }
    assert table["distance_km"].sum() == pytest.approx(result.total_distance_m / 1000, abs=0.5)


async def test_cluster_size_limit_is_respected(sites):
    result = await plan_routes(sites, DEPOTS, settings=Settings(
        distance_method="haversine", max_sites_per_cluster=5, solver_time_limit_s=2
    ))
    sizes = result.sites.groupby(["depot", "cluster"]).size()
    assert sizes.max() <= 5


async def test_depot_is_node_zero_in_every_cluster(sites):
    """База обязана быть нулевым узлом каждого кластера.

    Без неё маршрут не замыкается: машина обязана выехать с базы и вернуться
    на неё, а солвер об этом узнаёт только из матрицы расстояний, где нулевой
    узел — база. Это ровно то место, где ошибка не проявится исключением,
    а тихо испортит результат.
    """
    result = await plan_routes(sites, DEPOTS, settings=FAST)
    for (depot_id, _), solution in result.solutions.items():
        for route in solution.routes:
            assert route.nodes[0] == 0, "маршрут не начинается на базе"
            assert route.nodes[-1] == 0, "маршрут не заканчивается на базе"
            assert 0 not in route.nodes[1:-1], "база стала промежуточной остановкой"


async def test_depot_demand_does_not_consume_capacity(sites):
    """У базы нулевой спрос: иначе она съедала бы вместимость машины."""
    result = await plan_routes(sites, DEPOTS, settings=FAST)
    served = sum(
        len(r.nodes) - 2 for s in result.solutions.values() for r in s.routes
    )
    assert served + len(result.unserved) == len(sites)


async def test_every_point_is_visited_exactly_once(sites):
    result = await plan_routes(sites, DEPOTS, settings=FAST)
    for (depot_id, cluster_id), solution in result.solutions.items():
        chunk = result.sites[
            (result.sites["depot"] == depot_id) & (result.sites["cluster"] == cluster_id)
        ]
        visited = [n for r in solution.routes for n in r.nodes[1:-1]]
        assert len(visited) == len(set(visited)), "точка обслужена дважды"
        assert set(visited) | set(solution.dropped) == set(range(1, len(chunk) + 1))

# Точки вытянуты в линию от базы: так оценка парка заведомо занижена.
# Она берёт для каждой точки ближайшего соседа, а на линии машина обязана
# доехать до дальнего конца и вернуться — вдвое больше. Именно в этом зазоре
# и живут потери, ради которых написаны два теста ниже.
STRUNG_OUT_DEPOT = (55.0, 61.0)


@pytest.fixture
def strung_out_sites():
    return pd.DataFrame(
        [
            {"id": f"p{k}", "lat": 55.0, "lon": 61.0 + 0.05 * k, "demand": 100}
            for k in range(1, 11)
        ]
    )


TIGHT = dict(
    distance_method="haversine",
    max_sites_per_cluster=50,
    vehicle_capacity=10_000,     # вместимости хватает с запасом: дело не в ней
    vehicle_max_time_min=160,    # а в смене — на всю линию нужно вдвое больше
    service_time_min=10,
    solver_time_limit_s=2,
)


async def test_solver_drops_are_reported_not_swallowed(strung_out_sites):
    """Точки, брошенные солвером, обязаны быть видны наружу.

    Регрессия: PlanResult.unassigned покрывал только нехватку мощности баз,
    а точки, брошенные солвером из-за нехватки машин или времени, наружу не
    выходили вовсе — метрика показывала ноль при реально потерянной точке.
    """
    settings = Settings(**TIGHT, auto_add_vehicles=0)  # добор выключен: проверяем отчёт
    result = await plan_routes(strung_out_sites, [STRUNG_OUT_DEPOT], settings=settings)

    dropped_in_solutions = sum(len(s.dropped) for s in result.solutions.values())
    assert dropped_in_solutions > 0, "ожидались брошенные точки"
    assert len(result.dropped) == dropped_in_solutions
    assert set(result.dropped) <= set(result.sites.index)
    assert result.unassigned == [], "базы тут ни при чём, мощности хватает"
    assert set(result.unserved) == set(result.dropped)


async def test_auto_add_vehicles_rescues_dropped_points(strung_out_sites):
    """Добор машин должен спасать точки, которые иначе теряются."""
    without = await plan_routes(
        strung_out_sites, [STRUNG_OUT_DEPOT],
        settings=Settings(**TIGHT, auto_add_vehicles=0),
    )
    with_extra = await plan_routes(
        strung_out_sites, [STRUNG_OUT_DEPOT],
        settings=Settings(**TIGHT, auto_add_vehicles=2),
    )
    assert without.dropped, "сценарий потерял смысл: без добора никто не брошен"
    assert len(with_extra.dropped) < len(without.dropped)
    assert with_extra.vehicles_used > without.vehicles_used


async def test_explicit_fleet_is_not_silently_enlarged(strung_out_sites):
    """Явно заданный парк — ограничение задачи, добор его трогать не вправе."""
    result = await plan_routes(
        strung_out_sites, [STRUNG_OUT_DEPOT],
        settings=Settings(**TIGHT, auto_add_vehicles=2),
        fleet=Fleet(count=1, capacity=10_000, max_time_min=160,
                    speed_kmh=40.0, service_time_min=10),
    )
    assert result.vehicles_used == 1
    assert result.dropped, "при одной машине линию не объехать целиком"
