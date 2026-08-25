"""HTTP-обёртка над пайплайном routeforge.

    uvicorn services.api.main:app --reload

Сервис без состояния: расчёт целиком укладывается в один запрос, ничего
между вызовами не хранится. Метрики отдаются на ``/metrics`` в формате
Prometheus.
"""

from __future__ import annotations

import os
import time

import pandas as pd
from fastapi import FastAPI, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

from routeforge.config import Settings
from routeforge.pipeline import plan_routes

app = FastAPI(
    title="routeforge API",
    version="0.1.0",
    description="Планирование объезда точек: базы, кластеры, маршруты.",
)

REQUESTS = Counter("routeforge_plan_requests_total", "Запросы на расчёт", ["status"])
DURATION = Histogram("routeforge_plan_duration_seconds", "Время расчёта")
POINTS = Histogram(
    "routeforge_plan_points", "Точек в запросе", buckets=(10, 50, 100, 500, 1000, 5000)
)


class Point(BaseModel):
    id: str | None = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    demand: float = 0.0


class Depot(BaseModel):
    id: str | None = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    capacity: float | None = None


class PlanRequest(BaseModel):
    sites: list[Point] = Field(min_length=1)
    depots: list[Depot] = Field(min_length=1)
    distance_method: str = "haversine"
    vehicle_capacity: int = 4000
    vehicle_max_time_min: int = 480
    max_sites_per_cluster: int = 200
    solver_time_limit_s: int = 10


class RouteOut(BaseModel):
    depot: int
    cluster: int
    vehicle: int
    stops: int
    distance_km: float
    duration_min: int
    load: int


class PlanResponse(BaseModel):
    routes: list[RouteOut]
    vehicles_used: int
    total_distance_km: float
    unassigned: list[int]
    elapsed_s: float


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/plan", response_model=PlanResponse)
async def plan(request: PlanRequest) -> PlanResponse:
    started = time.perf_counter()
    POINTS.observe(len(request.sites))

    settings = Settings(
        distance_method=request.distance_method,
        osrm_url=os.environ.get("ROUTEFORGE_OSRM_URL", "http://osrm:5000"),
        vehicle_capacity=request.vehicle_capacity,
        vehicle_max_time_min=request.vehicle_max_time_min,
        max_sites_per_cluster=request.max_sites_per_cluster,
        solver_time_limit_s=request.solver_time_limit_s,
    )

    sites = pd.DataFrame(
        [{"id": s.id or i, "lat": s.lat, "lon": s.lon, "demand": s.demand}
         for i, s in enumerate(request.sites)]
    )
    depots = [(d.lat, d.lon) for d in request.depots]
    capacities = (
        [d.capacity for d in request.depots]
        if all(d.capacity is not None for d in request.depots)
        else None
    )

    try:
        with DURATION.time():
            result = await plan_routes(
                sites, depots, settings=settings, depot_capacities=capacities
            )
    except (ValueError, RuntimeError) as exc:
        REQUESTS.labels(status="error").inc()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    REQUESTS.labels(status="ok").inc()
    table = result.routes_table()
    return PlanResponse(
        routes=[RouteOut(**row) for row in table.to_dict("records")],
        vehicles_used=result.vehicles_used,
        total_distance_km=round(result.total_distance_m / 1000, 2),
        unassigned=result.unassigned,
        elapsed_s=round(time.perf_counter() - started, 3),
    )
