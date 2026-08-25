# Локальный OSRM

Без OSRM `routeforge` считает расстояния по прямой. Этого достаточно, чтобы
запустить демо и разобраться в схеме, но для реального планирования нужны
дорожные расстояния: на городской сети разница доходит до полутора раз, и
маршрут, оптимальный по прямой, на дорогах может оказаться заметно хуже.

## Поднять за пару минут

Не берите сразу выгрузку на всю страну: `russia-latest.osm.pbf` весит около
3,5 ГБ, а подготовка занимает часы и десятки гигабайт памяти. Начните с
маленького региона — механика та же.

```bash
mkdir osrm-data && cd osrm-data

# Монако — 600 КБ, готовится за секунды
curl -O https://download.geofabrik.de/europe/monaco-latest.osm.pbf

docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
    osrm-extract -p /opt/car.lua /data/monaco-latest.osm.pbf
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
    osrm-partition /data/monaco-latest.osrm
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
    osrm-customize /data/monaco-latest.osrm

docker run -t -i -p 5000:5000 -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
    osrm-routed --algorithm mld /data/monaco-latest.osrm
```

Проверка:

```bash
curl "http://localhost:5000/route/v1/driving/7.419,43.731;7.427,43.737?overview=false"
```

## Подключить

```python
from routeforge.config import Settings

settings = Settings(distance_method="osrm", osrm_url="http://localhost:5000")
```

или через окружение:

```bash
export ROUTEFORGE_DISTANCE_METHOD=osrm
export ROUTEFORGE_OSRM_URL=http://localhost:5000
```

## Что стоит знать

**Матрицы считаются через `/table`, а не по паре точек.** Один запрос
возвращает целую строку матрицы. На задаче «7 баз × 1000 точек» это 84
запроса вместо 7000.

**Есть предел размера таблицы.** OSRM по умолчанию ограничивает
`max-table-size` сотней координат, поэтому длинные списки режутся на куски по
`DEFAULT_TABLE_CHUNK` (90). Предел поднимается флагом:

```bash
osrm-routed --algorithm mld --max-table-size 1000 /data/region.osrm
```

**Число одновременных запросов ограничено.** `DEFAULT_CONCURRENCY` = 32.
Заваливать сервер тысячами соединений контрпродуктивно: растёт задержка, а
пропускная способность не увеличивается.

**Порядок координат — `lon,lat`.** Внутри `routeforge` координаты везде
`(lat, lon)`, и переворачиваются ровно в одном месте — при сборке URL.
Перепутанный порядок не даёт ошибки, он молча уводит маршрут в другую страну.

## Большие регионы

Для области или страны понадобится машина с запасом памяти: подготовка
`russia-latest.osm.pbf` требует порядка 32 ГБ RAM и нескольких часов. Разумнее
готовить один раз, складывать готовые `.osrm*` файлы и поднимать `osrm-routed`
уже на них.
