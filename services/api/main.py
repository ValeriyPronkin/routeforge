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
from routeforge.fleet import Vehicle
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


class VehicleIn(BaseModel):
    """Одна машина реестра.

    Обязательна только вместимость. Смена, если не указана, берётся из
    ``vehicle_max_time_min`` запроса, а база — из ``id`` базы, а не из её
    номера в массиве: позиционная ссылка молча уезжает не туда, стоит
    переставить базы местами.
    """

    id: str | None = None
    capacity: float = Field(gt=0)
    max_time_min: int | None = Field(default=None, gt=0)
    depot: str | None = None


class PlanRequest(BaseModel):
    sites: list[Point] = Field(min_length=1)
    depots: list[Depot] = Field(min_length=1)
    #: Реестр машин. Отсутствует — парк однородный и его размер подбирает
    #: расчёт. Задан — парк становится ограничением задачи. Пустой список
    #: это не «считай по оценке», а «машин нет», и он отвергается.
    vehicles: list[VehicleIn] | None = None
    distance_method: str = "haversine"
    vehicle_capacity: int = 4000
    vehicle_max_time_min: int = 480
    max_sites_per_cluster: int = 200
    solver_time_limit_s: int = 10
    vehicle_min_remaining_min: int = 40
    fleet_capacity_reserve: float = 0.0


class RouteOut(BaseModel):
    depot: int
    cluster: int
    vehicle: int
    #: Номер машины из реестра; пусто, когда расчёт шёл без него.
    vehicle_id: str = ""
    stops: int
    distance_km: float
    duration_min: int
    load: int


class VehicleOut(BaseModel):
    """Строка сводки: чем машина была занята за день."""

    vehicle_id: str
    routes: int
    stops: int
    distance_km: float
    load: int
    duration_min: int
    shift_used_pct: int
    remaining_min: int


class PlanResponse(BaseModel):
    routes: list[RouteOut]
    #: Сводка по машинам реестра, включая не выезжавшие. Пусто без реестра.
    vehicles: list[VehicleOut] = []
    vehicles_used: int
    total_distance_km: float
    #: Точки, которые не приняла ни одна база: не хватило мощности.
    unassigned: list[int]
    #: Точки, которые бросил солвер: не хватило машин или времени смены.
    #: Отдельное поле, потому что лечится это другим — см. docs/algorithm.md.
    dropped: list[int] = []
    elapsed_s: float = 0.0


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _build_registry(request: PlanRequest) -> list[Vehicle] | None:
    """Превращает реестр запроса в машины пайплайна.

    Здесь же ловятся ошибки, которые дальше стали бы тихой неправдой:
    склеенные в сводке одинаковые номера и ссылка на несуществующую базу.
    """
    if request.vehicles is None:
        return None
    if not request.vehicles:
        raise HTTPException(
            status_code=422,
            detail="vehicles пуст: это значит «машин нет». Чтобы считать по "
            "оценке, поле передавать не нужно.",
        )

    numbers = [v.id or f"ТС {i}" for i, v in enumerate(request.vehicles)]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        raise HTTPException(
            status_code=422,
            detail=f"Номера машин повторяются: {duplicates}. Сводка склеила бы "
            "их в одну строку.",
        )

    known = {d.id: i for i, d in enumerate(request.depots) if d.id is not None}
    vehicles = []
    for number, item in zip(numbers, request.vehicles):
        depot = None
        if item.depot is not None:
            if item.depot not in known:
                raise HTTPException(
                    status_code=422,
                    detail=f"Машина {number} приписана к базе {item.depot!r}, "
                    f"которой нет в запросе. Известные базы: {sorted(known)}.",
                )
            depot = known[item.depot]
        vehicles.append(
            Vehicle(
                id=number,
                capacity=int(item.capacity),
                max_time_min=int(item.max_time_min or request.vehicle_max_time_min),
                depot=depot,
            )
        )
    return vehicles


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
        vehicle_min_remaining_min=request.vehicle_min_remaining_min,
        fleet_capacity_reserve=request.fleet_capacity_reserve,
    )
    vehicles = _build_registry(request)

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
                sites, depots, settings=settings,
                depot_capacities=capacities, vehicles=vehicles,
            )
    except (ValueError, RuntimeError) as exc:
        REQUESTS.labels(status="error").inc()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    REQUESTS.labels(status="ok").inc()
    table = result.routes_table()
    summary = result.vehicles_table()
    return PlanResponse(
        routes=[RouteOut(**row) for row in table.to_dict("records")],
        vehicles=[VehicleOut(**row) for row in summary.to_dict("records")],
        vehicles_used=result.vehicles_used,
        total_distance_km=round(result.total_distance_m / 1000, 2),
        unassigned=result.unassigned,
        dropped=result.dropped,
        elapsed_s=round(time.perf_counter() - started, 3),
    )
