import numpy as np
import pytest

from routeforge.distance import haversine_matrix
from routeforge.solver import Fleet, SolverConfig, estimate_vehicles, solve_cvrp

# Депо в центре, четыре точки вокруг.
POINTS = [(55.75, 37.61), (55.76, 37.62), (55.77, 37.60), (55.74, 37.64), (55.78, 37.63)]
FAST = SolverConfig(time_limit_s=2)


@pytest.fixture
def matrix():
    return haversine_matrix(POINTS, POINTS)


def test_single_vehicle_visits_every_point(matrix):
    demands = [0, 1, 1, 1, 1]
    sol = solve_cvrp(matrix, demands, Fleet(count=1, capacity=10), depot=0, config=FAST)
    assert sol.dropped == []
    assert len(sol.routes) == 1
    assert sorted(set(sol.routes[0].nodes)) == [0, 1, 2, 3, 4]


def test_route_starts_and_ends_at_depot(matrix):
    sol = solve_cvrp(matrix, [0, 1, 1, 1, 1], Fleet(count=1, capacity=10), depot=0, config=FAST)
    nodes = sol.routes[0].nodes
    assert nodes[0] == 0 and nodes[-1] == 0


def test_capacity_forces_a_second_vehicle(matrix):
    demands = [0, 10, 10, 10, 10]
    sol = solve_cvrp(matrix, demands, Fleet(count=2, capacity=20), depot=0, config=FAST)
    assert sol.dropped == []
    assert len(sol.routes) == 2
    for route in sol.routes:
        assert route.load <= 20


def test_points_are_dropped_when_capacity_is_short(matrix):
    # Одна машина на 20 единиц не увезёт 40 — часть точек придётся бросить.
    sol = solve_cvrp(matrix, [0, 10, 10, 10, 10], Fleet(count=1, capacity=20), depot=0, config=FAST)
    assert sol.dropped, "ожидались пропущенные точки"
    assert sol.routes[0].load <= 20


def test_drop_penalty_none_makes_the_problem_infeasible(matrix):
    sol = solve_cvrp(
        matrix,
        [0, 10, 10, 10, 10],
        Fleet(count=1, capacity=20),
        depot=0,
        config=SolverConfig(time_limit_s=2, drop_penalty=None),
    )
    assert sol.routes == [] and sol.total_distance_m == 0


def test_separate_start_and_end_depots():
    # Гараж и полигон — разные точки: выезд из 0, возврат в 1.
    points = [(55.70, 37.50), (55.85, 37.80), (55.75, 37.61), (55.78, 37.65)]
    matrix = haversine_matrix(points, points)
    sol = solve_cvrp(
        matrix, [0, 0, 1, 1], Fleet(count=1, capacity=10), starts=[0], ends=[1], config=FAST
    )
    assert sol.routes[0].nodes[0] == 0
    assert sol.routes[0].nodes[-1] == 1


def test_shift_limit_shows_up_in_duration(matrix):
    fleet = Fleet(count=1, capacity=10, max_time_min=600, speed_kmh=40, service_time_min=10)
    sol = solve_cvrp(matrix, [0, 1, 1, 1, 1], fleet, depot=0, config=FAST)
    assert 0 < sol.routes[0].duration_min <= 600


def test_estimate_vehicles_covers_total_demand():
    assert estimate_vehicles([10, 10, 10], capacity=10) == 3
    assert estimate_vehicles([10, 10, 10], capacity=100) == 1


def test_rejects_non_square_matrix():
    with pytest.raises(ValueError, match="квадратной"):
        solve_cvrp(np.zeros((2, 3)), [0, 0], Fleet(count=1, capacity=1))


def test_rejects_mismatched_demands(matrix):
    with pytest.raises(ValueError, match="demands"):
        solve_cvrp(matrix, [0, 1], Fleet(count=1, capacity=1))


def test_starts_and_ends_must_come_together(matrix):
    with pytest.raises(ValueError, match="вместе"):
        solve_cvrp(matrix, [0, 1, 1, 1, 1], Fleet(count=1, capacity=10), starts=[0])
