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


REGISTRY_PAYLOAD = {
    **PAYLOAD,
    "vehicles": [
        {"id": "А101ВС", "capacity": 500, "max_time_min": 480, "depot": "D0"},
        {"id": "А102ВС", "capacity": 600},
    ],
}


def test_plan_with_registry_names_the_machines():
    body = client.post("/plan", json=REGISTRY_PAYLOAD).json()
    assert body["routes"], "маршруты ожидались"
    assert all(r["vehicle_id"] in {"А101ВС", "А102ВС"} for r in body["routes"])


def test_summary_covers_every_vehicle_of_the_registry():
    """В сводке обе машины, даже если работала одна: простой тоже результат."""
    body = client.post("/plan", json=REGISTRY_PAYLOAD).json()
    assert {v["vehicle_id"] for v in body["vehicles"]} == {"А101ВС", "А102ВС"}
    for row in body["vehicles"]:
        assert row["remaining_min"] <= 480
        assert 0 <= row["shift_used_pct"] <= 100


def test_without_registry_summary_is_empty():
    body = client.post("/plan", json=PAYLOAD).json()
    assert body["vehicles"] == []
    assert all(r["vehicle_id"] == "" for r in body["routes"])


def test_empty_registry_is_rejected():
    """Пустой список — это «машин нет», а не «считай по оценке»."""
    response = client.post("/plan", json={**PAYLOAD, "vehicles": []})
    assert response.status_code == 422
    assert "машин нет" in response.json()["detail"]


def test_duplicate_vehicle_numbers_are_rejected():
    payload = {
        **PAYLOAD,
        "vehicles": [{"id": "А101ВС", "capacity": 500}, {"id": "А101ВС", "capacity": 600}],
    }
    response = client.post("/plan", json=payload)
    assert response.status_code == 422
    assert "А101ВС" in response.json()["detail"]


def test_unknown_depot_reference_is_rejected_with_the_known_ones():
    payload = {**PAYLOAD, "vehicles": [{"id": "А101ВС", "capacity": 500, "depot": "D9"}]}
    response = client.post("/plan", json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "D9" in detail and "D0" in detail


def test_vehicle_without_capacity_is_rejected():
    response = client.post("/plan", json={**PAYLOAD, "vehicles": [{"id": "А", "capacity": 0}]})
    assert response.status_code == 422


def test_missing_shift_falls_back_to_the_request_default():
    payload = {
        **PAYLOAD,
        "vehicle_max_time_min": 120,
        "vehicles": [{"id": "А101ВС", "capacity": 10_000}],
    }
    body = client.post("/plan", json=payload).json()
    row = body["vehicles"][0]
    assert row["duration_min"] + row["remaining_min"] <= 120
