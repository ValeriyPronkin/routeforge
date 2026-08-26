# routeforge

Планировщик объезда множества точек по схеме **cluster-first, route-second**.

Задача одна и та же независимо от предметной области: есть сотни или тысячи
точек, которые надо объехать, несколько баз и парк машин с ограниченной
вместимостью и длительностью смены. Требуется распределить точки по базам,
разбить их на выполнимые за смену группы и построить маршруты. Так решается
объезд контейнерных площадок, развозка по магазинам, обслуживание
оборудования на объектах.

![Интерфейс routeforge](docs/screenshot.png)

*Демо-набор: 240 точек, 3 базы, 6 кластеров, 10 маршрутов. Цвет плашки в таблице
совпадает с цветом маршрута на карте; точки можно раскрасить по базам или по
кластерам. Запускается командой из раздела «Быстрый старт».*

| Кому | Куда |
|---|---|
| Готовлю данные для расчёта | [docs/data.md](docs/data.md) |
| Разбираюсь, как считается | [docs/algorithm.md](docs/algorithm.md) |
| Читаю отчёт, выгружаю результат | [docs/reports.md](docs/reports.md) |
| Вызываю из своей системы | [docs/api.md](docs/api.md) |
| Ставлю OSRM для дорожных расстояний | [docs/osrm.md](docs/osrm.md) |

## Почему в два шага

Решать CVRP на всём множестве точек сразу не выходит: время решения растёт
быстрее линейного, и одна задача на 5000 точек считается дольше, чем десять
по 500. Поэтому сначала точки распределяются по базам с учётом их мощности,
затем крупные группы дробятся до размера, посильного солверу, и CVRP решается
внутри каждой группы независимо.

![Два шага метода](docs/demo.png)

*Слева — шаг первый: точки распределены по базам. Справа — шаг второй: внутри
каждой группы решён CVRP, получилось 10 маршрутов общей длиной 604 км.
Воспроизводится скриптом `scripts/make_demo_assets.py`; числа могут немного
отличаться — солвер недетерминирован.*

Расстояния можно считать двумя способами. По прямой (haversine) — мгновенно и
без инфраструктуры, годится для кластеризации, где важен порядок близости.
По дорогам через [OSRM](https://project-osrm.org/) — то, что реально проедет
машина; разница на городской сети доходит до полутора раз.

## Быстрый старт

```bash
git clone https://github.com/ValeriyPronkin/routeforge
cd routeforge
pip install -e ".[app,api,dev]"

python scripts/make_sample_data.py     # 240 точек, 3 базы, 6 машин
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

## Настройки

Файл `config.yaml` рядом с проектом, необязательный — без него берутся
значения по умолчанию. Любое поле переопределяется переменной окружения с
префиксом `ROUTEFORGE_`:

```yaml
common:
    app_title: 'Логистика'          # как приложение называет себя на экране
    distance_method: 'haversine'    # haversine или osrm
    osrm_url: 'http://localhost:5000'
    log_dir: 'logs'
    log_level: 'INFO'
```

```bash
ROUTEFORGE_APP_TITLE='План на завтра' streamlit run app/streamlit_app.py
```

## Логи

Журнал пишется в `logs/routeforge.log` — путь задаётся `log_dir`, ротация по
10 МБ, хранится пять файлов. Путь показан в панели слева, чтобы его не
приходилось искать.

При запуске из терминала те же строки идут в консоль. Если запускали в
фоне, смотрите файл:

```bash
tail -f logs/routeforge.log
```

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
| `routeforge.fleet` | реестр реальных машин и раздача их по кластерам с остатком смены |
| `routeforge.pipeline` | всё перечисленное одним вызовом |

Входной формат нарочно простой — таблица с колонками `lat`, `lon`, `demand`.
Заголовки распознаются и русские (`Широта`, `Долгота`, `Спрос`, `Вес`), и
английские; координаты вида `55,7558` с запятой разбираются, строки с
битыми координатами отбрасываются с указанием, сколько и каких. Свои
заголовки добавляются одной строкой — см. `COLUMN_ALIASES` в
`routeforge/io.py`.

Одну ловушку стоит держать в голове: маршрутизация без спроса — допустимый
сценарий, поэтому нераспознанная колонка спроса не ошибка, а ноль. Если
колонка в файле есть, но названа непривычно, все точки получат нулевой спрос
и солвер построит один маршрут на всё. Сверьтесь с `COLUMN_ALIASES`.

## Данные

Демо-набор синтетический, генерируется скриптом. Настоящие выгрузки, на
которых инструмент работает, содержат адресную привязку объектов и в открытый
доступ не выкладываются.

## Документация

* [docs/data.md](docs/data.md) — входные файлы и термины
* [docs/algorithm.md](docs/algorithm.md) — как считается, шаг за шагом
* [docs/reports.md](docs/reports.md) — отчёт и выгрузка
* [docs/api.md](docs/api.md) — HTTP-сервис
* [docs/osrm.md](docs/osrm.md) — OSRM для дорожных расстояний
* [notebooks/](notebooks/) — кластеризация, расстояния, решение CVRP

## Разработка

```bash
pip install -e ".[app,api,dev]"
pytest
```

## Как делался проект

Код писался в паре с Claude Code, и в истории коммитов это видно: соавторство
проставлено явно, а не скрыто. Раз уж оно видно, стоит сказать, как
распределялась работа.

**Со стороны человека:** метод и предметные решения, добытые за месяцы работы
с настоящими данными. Схема cluster-first, route-second. Раздача машин по
остатку смены — с порогом, ниже которого машину на линию не выводят. То, что
потери точек бывают двух видов и лечатся разным: мощностью баз или парком.
То, что базы и точки — разные сущности, и готовит их файлы пользователь, а
не угадывает алгоритм. Плюс проверка на реальных реестрах, где эти правила и
были найдены.

**Совместно:** реализация, тесты, документация.

Порядок был такой: сначала рабочая версия, сделанная руками и проверенная на
настоящих расчётах, потом — эта, написанная заново по уже понятному методу.
Второе быстрее первого на порядок именно потому, что первое было.

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

See [Quick start](#быстрый-старт) above; the demo runs without Docker or API keys.
