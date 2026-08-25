import numpy as np
import pytest

from routeforge.distance import (
    _osrm_coords,
    distance_matrix,
    haversine_distance,
    haversine_matrix,
)

MOSCOW = (55.7558, 37.6176)
SPB = (59.9343, 30.3351)


def test_haversine_matches_known_distance():
    # Москва - Петербург по большому кругу: ~634 км.
    d = haversine_distance(MOSCOW, SPB)
    assert 630_000 < d < 640_000


def test_haversine_is_symmetric_and_zero_on_diagonal():
    assert haversine_distance(MOSCOW, SPB) == pytest.approx(haversine_distance(SPB, MOSCOW))
    assert haversine_distance(MOSCOW, MOSCOW) == pytest.approx(0.0)


def test_matrix_shape_and_agreement_with_scalar():
    points = [MOSCOW, SPB, (56.8389, 60.6057)]
    m = haversine_matrix(points, points)
    assert m.shape == (3, 3)
    assert np.all(np.diag(m) == 0)
    assert m[0, 1] == pytest.approx(haversine_distance(MOSCOW, SPB), rel=1e-6)


def test_matrix_handles_empty_input():
    assert haversine_matrix([], [MOSCOW]).shape == (0, 1)
    assert haversine_matrix([MOSCOW], []).shape == (1, 0)


def test_osrm_url_uses_lon_lat_order():
    # OSRM ждёт lon,lat — перепутанный порядок молча уводит маршрут в другую страну.
    assert _osrm_coords([MOSCOW]) == "37.6176,55.7558"
    assert _osrm_coords([MOSCOW, SPB]) == "37.6176,55.7558;30.3351,59.9343"


async def test_distance_matrix_rejects_unknown_method():
    with pytest.raises(ValueError, match="Неизвестный метод"):
        await distance_matrix([MOSCOW], [SPB], method="magic")


async def test_distance_matrix_requires_url_for_osrm():
    with pytest.raises(ValueError, match="osrm_url"):
        await distance_matrix([MOSCOW], [SPB], method="osrm")
