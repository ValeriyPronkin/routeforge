import pytest

pytest.importorskip("fastapi", reason="сервисный контур ставится отдельно: pip install -e '.[api]'")

from fastapi.testclient import TestClient  # noqa: E402

from services.api.main import app  # noqa: E402

client = TestClient(app)

PAYLOAD = {
    "sites": [
        {"id": "1", "lat": 55.31, "lon": 61.50, "demand": 300},
        {"id": "2", "lat": 55.33, "lon": 61.52, "demand": 250},
        {"id": "3", "lat": 55.29, "lon": 61.46, "demand": 400},
    ],
    "depots": [{"id": "D0", "lat": 55.31, "lon": 61.48, "capacity": 10_000}],
    "vehicle_capacity": 1000,
    "solver_time_limit_s": 2,
}


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_plan_returns_routes():
    response = client.post("/plan", json=PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["vehicles_used"] >= 1
    assert body["total_distance_km"] > 0
    assert body["unassigned"] == []
    # Потери солвера — отдельное поле ответа: без него клиент считал бы, что
    # обслужены все точки, хотя часть могла остаться без маршрута.
    assert body["dropped"] == []
    assert len(body["routes"]) == body["vehicles_used"]


def test_capacity_forces_more_vehicles():
    tight = {**PAYLOAD, "vehicle_capacity": 300}
    assert client.post("/plan", json=tight).json()["vehicles_used"] > 1


def test_rejects_impossible_latitude():
    bad = {**PAYLOAD, "sites": [{"lat": 999, "lon": 0, "demand": 1}]}
    assert client.post("/plan", json=bad).status_code == 422


def test_rejects_empty_input():
    assert client.post("/plan", json={"sites": [], "depots": []}).status_code == 422


def test_metrics_exposes_counters():
    client.post("/plan", json=PAYLOAD)
    body = client.get("/metrics").text
    assert "routeforge_plan_requests_total" in body
    assert "routeforge_plan_duration_seconds" in body
