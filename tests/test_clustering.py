import numpy as np
import pytest

from routeforge.clustering import (
    UNASSIGNED,
    assign_to_depots,
    balanced_clusters,
    centroids,
    kmeans_labels,
    optimal_k_by_cluster_size,
)


@pytest.fixture
def coords():
    rng = np.random.default_rng(0)
    return np.column_stack([rng.uniform(55, 56, 100), rng.uniform(37, 38, 100)])


def test_assign_picks_nearest_depot_when_capacity_allows():
    distances = np.array([[10.0, 200.0, 300.0]])
    assert assign_to_depots(distances, demands=[5.0], capacities=[100.0, 100.0, 100.0]) == [0]


def test_assign_skips_depot_without_capacity():
    """Регрессия: ближайшее депо не может принять груз — берём следующее.

    В исходной реализации ёмкость проверялась у соседнего депо из-за
    двойного декремента индекса, поэтому площадка уезжала к переполненному
    приёмнику, а списание уходило вообще третьему.
    """
    distances = np.array([[10.0, 200.0, 300.0]])
    result = assign_to_depots(distances, demands=[500.0], capacities=[0.0, 1000.0, 1000.0])
    assert result.tolist() == [1]


def test_assign_deducts_from_the_depot_it_assigned_to():
    distances = np.array([[10.0, 20.0], [10.0, 20.0], [10.0, 20.0]])
    # Первое депо вмещает ровно две точки по 50.
    result = assign_to_depots(distances, demands=[50.0] * 3, capacities=[100.0, 50.0])
    assert sorted(result.tolist()) == [0, 0, 1]


def test_assign_marks_unservable_points():
    distances = np.array([[10.0]])
    assert assign_to_depots(distances, demands=[5.0], capacities=[0.0]).tolist() == [UNASSIGNED]


def test_assign_without_capacities_always_takes_nearest():
    distances = np.array([[30.0, 10.0], [5.0, 50.0]])
    assert assign_to_depots(distances).tolist() == [1, 0]


def test_optimal_k_survives_small_input(coords):
    """Регрессия: на входе <= 150 точек прежняя версия падала с UnboundLocalError,
    потому что range(1, round(len/100)) вырождался в пустой."""
    k, labels = optimal_k_by_cluster_size(coords, max_per_cluster=1000)
    assert k == 1
    assert len(labels) == len(coords)


def test_optimal_k_respects_the_limit(coords):
    k, labels = optimal_k_by_cluster_size(coords, max_per_cluster=30)
    assert np.bincount(labels).max() <= 30
    assert k >= int(np.ceil(len(coords) / 30))


def test_optimal_k_handles_empty_input():
    k, labels = optimal_k_by_cluster_size(np.empty((0, 2)))
    assert k == 0 and len(labels) == 0


def test_balanced_clusters_terminates_on_impossible_constraints(coords):
    """Регрессия: при недостижимых min/max прежняя версия уходила в бесконечный
    while True, так как n_clusters пересчитывался как max(const, n_clusters)."""
    k, labels = balanced_clusters(coords[:20], min_size=8, max_size=10)
    assert k >= 1
    assert len(labels) == 20


def test_balanced_clusters_hits_feasible_bounds(coords):
    k, labels = balanced_clusters(coords, min_size=10, max_size=40)
    sizes = np.bincount(labels)
    assert sizes.min() >= 10 and sizes.max() <= 40


def test_kmeans_is_deterministic(coords):
    assert np.array_equal(kmeans_labels(coords, 5), kmeans_labels(coords, 5))


def test_centroids_lie_inside_the_cloud(coords):
    labels = kmeans_labels(coords, 4)
    c = centroids(coords, labels)
    assert c.shape == (4, 2)
    assert c[:, 0].min() >= coords[:, 0].min() and c[:, 0].max() <= coords[:, 0].max()
