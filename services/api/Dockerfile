FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY routeforge ./routeforge
COPY services ./services

RUN pip install --no-cache-dir . fastapi uvicorn[standard] prometheus-client

EXPOSE 8000
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
