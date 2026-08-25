# Сервисный контур

## API

FastAPI поверх `routeforge.pipeline`. Без состояния: расчёт целиком
укладывается в один запрос.

```bash
pip install -e . fastapi "uvicorn[standard]" prometheus-client
uvicorn services.api.main:app --reload
```

Документация с интерактивной формой — на `http://localhost:8000/docs`.

Пример запроса:

```bash
curl -X POST http://localhost:8000/plan \
  -H 'Content-Type: application/json' \
  -d '{
    "sites": [
      {"id": "1", "lat": 55.31, "lon": 61.50, "demand": 300},
      {"id": "2", "lat": 55.33, "lon": 61.52, "demand": 250},
      {"id": "3", "lat": 55.29, "lon": 61.46, "demand": 400}
    ],
    "depots": [{"id": "D0", "lat": 55.31, "lon": 61.48, "capacity": 10000}],
    "vehicle_capacity": 1000,
    "solver_time_limit_s": 3
  }'
```

Эндпоинты:

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/plan` | расчёт маршрутов |
| `GET` | `/health` | проверка живости |
| `GET` | `/metrics` | метрики Prometheus |

## Docker

```bash
docker compose up api                      # только API
docker compose --profile road up           # + OSRM, нужен подготовленный osrm-data/
docker compose --profile monitoring up     # + Prometheus и Grafana
```

Для профиля `road` положите подготовленные файлы `region.osrm*` в `osrm-data/`
— как их получить, описано в [docs/osrm.md](../docs/osrm.md).

## Метрики

`routeforge_plan_requests_total{status}` — счётчик запросов,
`routeforge_plan_duration_seconds` — гистограмма времени расчёта,
`routeforge_plan_points` — гистограмма размера задачи.

Последняя нужнее, чем кажется: время расчёта осмысленно читать только
вместе с размером задачи, иначе рост p95 невозможно отличить от того, что
пользователи стали присылать более крупные выгрузки.
