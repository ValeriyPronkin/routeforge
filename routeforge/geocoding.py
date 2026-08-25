"""Адрес -> координаты.

Маршрутизатору нужны координаты, а исходные выгрузки часто содержат только
адреса. Модуль опциональный: если координаты уже есть, он не нужен.

Два провайдера:

``nominatim``
    Открытый геокодер OSM. Ключ не нужен, работает из коробки — поэтому
    он и стоит по умолчанию. Публичный сервер требует не больше одного
    запроса в секунду и осмысленного ``User-Agent``; тысячу адресов так
    геокодировать нельзя, для этого поднимают свой экземпляр.

``yandex``
    Точнее на российских адресах, но нужен ключ в ``YANDEX_API_KEY``
    и действуют лицензионные ограничения на хранение результата.

Пакетного режима нет ни у одного из них: и Nominatim, и Яндекс принимают
один адрес за запрос. Попытка склеить адреса через разделитель вернёт
координаты одной бессмысленной строки, а не список.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Sequence

import aiohttp

from .distance import Coord

USER_AGENT = "routeforge/0.1 (+https://github.com/ValeriyPronkin/routeforge)"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
YANDEX_URL = "https://geocode-maps.yandex.ru/1.x/"


@dataclass(frozen=True)
class GeocodeResult:
    """Результат по одному адресу."""

    query: str
    coord: Coord | None
    display_name: str | None = None

    @property
    def ok(self) -> bool:
        return self.coord is not None


async def _nominatim_one(session: aiohttp.ClientSession, address: str) -> GeocodeResult:
    params = {"q": address, "format": "json", "limit": "1"}
    async with session.get(NOMINATIM_URL, params=params) as response:
        response.raise_for_status()
        payload = await response.json()
    if not payload:
        return GeocodeResult(address, None)
    hit = payload[0]
    return GeocodeResult(address, (float(hit["lat"]), float(hit["lon"])), hit.get("display_name"))


async def _yandex_one(session: aiohttp.ClientSession, address: str, api_key: str) -> GeocodeResult:
    params = {"apikey": api_key, "format": "json", "geocode": address, "results": "1"}
    async with session.get(YANDEX_URL, params=params) as response:
        response.raise_for_status()
        payload = await response.json()
    members = payload["response"]["GeoObjectCollection"]["featureMember"]
    if not members:
        return GeocodeResult(address, None)
    obj = members[0]["GeoObject"]
    lon, lat = (float(v) for v in obj["Point"]["pos"].split())
    return GeocodeResult(address, (lat, lon), obj.get("metaDataProperty", {}).get("GeocoderMetaData", {}).get("text"))


async def geocode(
    addresses: Sequence[str],
    *,
    provider: str = "nominatim",
    api_key: str | None = None,
    rate_limit_s: float = 1.0,
    timeout: float = 30.0,
) -> list[GeocodeResult]:
    """Геокодирует список адресов, соблюдая паузу между запросами.

    :param rate_limit_s: пауза между запросами. Для публичного Nominatim
        меньше 1.0 ставить нельзя — забанят по IP. Для своего экземпляра
        или для Яндекса с оплаченным тарифом можно снижать.
    :returns: список той же длины и в том же порядке, что и ``addresses``;
        у ненайденных ``coord is None``.
    """
    provider = provider.lower()
    if provider == "yandex" and not api_key:
        raise ValueError("Для provider='yandex' нужен api_key (переменная YANDEX_API_KEY)")
    if provider not in {"nominatim", "yandex"}:
        raise ValueError(f"Неизвестный провайдер {provider!r}")

    results: list[GeocodeResult] = []
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(
        timeout=client_timeout, headers={"User-Agent": USER_AGENT}
    ) as session:
        for i, address in enumerate(addresses):
            if i:
                await asyncio.sleep(rate_limit_s)
            try:
                if provider == "nominatim":
                    results.append(await _nominatim_one(session, address))
                else:
                    results.append(await _yandex_one(session, address, api_key))  # type: ignore[arg-type]
            except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, ValueError):
                results.append(GeocodeResult(address, None))
    return results
