# routeforge

Планировщик объезда множества точек по схеме **cluster-first, route-second**.

Задача одна и та же независимо от предметной области: есть сотни или тысячи
точек, которые надо объехать, несколько баз и парк машин с ограниченной
вместимостью и длительностью смены. Требуется распределить точки по базам,
разбить их на выполнимые за смену группы и построить маршруты. Так решается
объезд контейнерных площадок, развозка по магазинам, обслуживание
оборудования на объектах.

![Пример расчёта](docs/demo.png)

*240 точек, 3 базы, машины по 4 тонны. Слева — распределение по базам, справа —
10 маршрутов общей длиной 602 км. Воспроизводится командой из раздела «Быстрый старт».*

## Почему в два шага

Решать CVRP на всём множестве точек сразу не выходит: время решения растёт
быстрее линейного, и одна задача на 5000 точек считается дольше, чем десять
по 500. Поэтому сначала точки распределяются по базам с учётом их мощности,
затем крупные группы дробятся до размера, посильного солверу, и CVRP решается
внутри каждой группы независимо.

Расстояния можно считать двумя способами. По прямой (haversine) — мгновенно и
без инфраструктуры, годится для кластеризации, где важен порядок близости.
По дорогам через [OSRM](https://project-osrm.org/) — то, что реально проедет
машина; разница на городской сети доходит до полутора раз.

## Быстрый старт

```bash
git clone https://github.com/ValeriyPronkin/routeforge
cd routeforge
pip install -e ".[app,dev]"

python scripts/make_sample_data.py     # 240 синтетических точек и 3 базы
streamlit run app/streamlit_app.py     # интерактивный расчёт в браузере
```

Или из кода:

```python
import pandas as pd
from routeforge.io import read_points
from routeforge.config import Settings
from routeforge.pipeline import plan_routes_sync

sites = read_points("data/sample/sites.csv")
depots_df = pd.read_csv("data/sample/depots.csv")
depots = list(depots_df[["lat", "lon"]].itertuples(index=False, name=None))

result = plan_routes_sync(
    sites, depots,
    settings=Settings(distance_method="haversine", vehicle_capacity=4000),
    depot_capacities=list(depots_df["capacity"]),
)

print(result.routes_table())
print(f"{result.vehicles_used} машин, {result.total_distance_m / 1000:.0f} км")
```

Демо работает без Docker и без ключей: по умолчанию расстояния считаются по
прямой. Для дорожных расстояний см. [docs/osrm.md](docs/osrm.md) — OSRM на
маленьком регионе поднимается за пару минут.

## Из чего состоит

| Модуль | Отвечает за |
|---|---|
| `routeforge.io` | чтение csv/xlsx, распознавание русских и английских заголовков, проверка координат |
| `routeforge.geocoding` | адрес → координаты через Nominatim или Яндекс, если координат нет |
| `routeforge.distance` | матрицы расстояний: haversine векторно, OSRM через `/table` асинхронно |
| `routeforge.clustering` | распределение по базам с учётом мощности, дробление крупных групп |
| `routeforge.solver` | CVRP на OR-Tools: вместимость, длительность смены, штраф за пропуск, раздельные точки старта и финиша |
| `routeforge.polylines` | геометрия маршрутов — прямые отрезки или реальные дороги |
| `routeforge.viz` | карты folium |
| `routeforge.pipeline` | всё перечисленное одним вызовом |

Входной формат нарочно простой — таблица с колонками `lat`, `lon`, `demand`.
Заголовки распознаются и русские (`Широта`, `Долгота`, `tko в день (кг)`), и
английские; координаты вида `55,7558` с запятой разбираются, строки с
битыми координатами отбрасываются с указанием, сколько и каких.

## Данные

Демо-набор генерируется скриптом и синтетический: реальные реестры мест
накопления отходов содержат адресную привязку объектов и в открытый доступ
не выкладываются.

## Документация

* [docs/algorithm.md](docs/algorithm.md) — что происходит на каждом шаге и какие параметры на что влияют
* [docs/osrm.md](docs/osrm.md) — как поднять OSRM локально
* [notebooks/](notebooks/) — кластеризация, сравнение способов расчёта расстояний, решение CVRP

## Разработка

```bash
pip install -e ".[app,dev]"
pytest
```

## Происхождение

Проект вырос из внутреннего инструмента для планирования объезда контейнерных
площадок при сборе ТКО и начинался как форк
[bruscalia12/tsp-app](https://github.com/bruscalia12/tsp-app) — оттуда идея
интерфейса на Streamlit. От исходного проекта в этой публичной версии кода не
осталось: солвер, кластеризация, работа с расстояниями и весь пайплайн
написаны заново.

## Лицензия

MIT, см. [LICENSE](LICENSE).

---

## English

**routeforge** is a cluster-first, route-second planner for capacitated vehicle
routing. Given many service points, several depots and a fleet with capacity
and shift-length limits, it assigns points to depots, splits large groups into
solver-sized chunks and solves a CVRP within each.

Distances come either from the haversine formula (instant, no infrastructure)
or from a local OSRM instance (real road network). Routing is done with
Google OR-Tools; maps are rendered with folium.

The domain it grew from is municipal waste collection, but the formulation is
generic — retail delivery and field service fit the same shape.

See [Quick start](#быстрый-старт) above; the demo runs without Docker or API keys.
