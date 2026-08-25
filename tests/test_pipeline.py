import pandas as pd
import pytest

from routeforge.clustering import UNASSIGNED
from routeforge.config import Settings
from routeforge.pipeline import plan_routes

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
