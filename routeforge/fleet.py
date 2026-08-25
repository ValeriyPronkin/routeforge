"""Реальный парк машин: реестр и раздача по кластерам.

Отличие от :class:`routeforge.solver.Fleet` в том, что здесь машины не
взаимозаменяемы. У каждой свой номер, своя вместимость и свой остаток
времени, который убывает по мере работы. Машина, отработавшая смену в одном
кластере, не может выйти в другом — именно это и мешает считать парк заново
на каждом кластере.

Раздача устроена так же, как в рабочем приложении, откуда взят метод:
машины сортируются по остатку времени, набираются, пока их суммарная
вместимость не покроет спрос кластера, а после расчёта из остатка вычитается
фактическое время маршрута. Машина с остатком меньше порога на линию больше
не выводится: за оставшиеся минуты она не успеет даже доехать.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .solver import Fleet


@dataclass
class Vehicle:
    """Одна машина реестра.

    :param id: номер, которым машина зовётся в жизни: госномер, инвентарный.
        Он идёт до самой таблицы маршрутов — иначе в отчёте остаётся
        безымянный индекс, по которому машину не найти.
    :param capacity: вместимость в единицах спроса.
    :param max_time_min: смена, полная.
    :param depot: индекс базы, к которой машина приписана. ``None`` — машина
        доступна любой базе.
    :param remaining_min: остаток времени. В начале равен смене.
    """

    id: str
    capacity: int
    max_time_min: int
    depot: int | None = None
    remaining_min: int = field(default=-1)

    def __post_init__(self) -> None:
        if self.remaining_min < 0:
            self.remaining_min = int(self.max_time_min)


class VehiclePool:
    """Парк, который помнит, кто сколько отработал.

    :param vehicles: реестр машин.
    :param min_remaining_min: порог, ниже которого машина считается
        отработавшей. В исходном приложении это было 40 минут, зашитых в код.
    :param capacity_reserve: запас вместимости при наборе машин на кластер,
        в единицах спроса. Ноль — набирать впритык.
    """

    def __init__(
        self,
        vehicles: list[Vehicle],
        *,
        min_remaining_min: int = 40,
        capacity_reserve: float = 0.0,
    ):
        self.vehicles = vehicles
        self.min_remaining_min = int(min_remaining_min)
        self.capacity_reserve = float(capacity_reserve)

    def available(self, depot: int | None = None) -> list[Vehicle]:
        """Машины, которые ещё могут выйти на линию из этой базы."""
        return [
            v
            for v in self.vehicles
            if v.remaining_min > self.min_remaining_min
            and (depot is None or v.depot is None or v.depot == depot)
        ]

    def pick(self, demand: float, depot: int | None = None) -> list[Vehicle]:
        """Набирает машины на кластер со спросом ``demand``.

        Сначала те, у кого больше остаток времени: свежая машина успеет
        больше, чем доработавшая. Набор прекращается, как только суммарной
        вместимости хватает на спрос с запасом — брать весь парк на один
        кластер незачем, остальным тоже нужно ехать.

        Возвращает пустой список, если свободных машин не осталось. Хватило
        их вместимости или нет, решает уже солвер: недостачу он покажет
        брошенными точками, а не молчанием.
        """
        free = sorted(self.available(depot), key=lambda v: v.remaining_min, reverse=True)
        picked: list[Vehicle] = []
        total = 0.0
        for vehicle in free:
            picked.append(vehicle)
            total += vehicle.capacity
            if total > demand + self.capacity_reserve:
                break
        return picked

    def spend(self, vehicle: Vehicle, minutes: float) -> None:
        """Списывает отработанное время.

        Остаток ниже порога обнуляется, а не оставляется мелочью: это то же
        самое решение «на линию не выводим», только записанное в данных, и
        оно избавляет от машин с пятью минутами в запасе.
        """
        vehicle.remaining_min = max(0, int(round(vehicle.remaining_min - minutes)))
        if vehicle.remaining_min <= self.min_remaining_min:
            vehicle.remaining_min = 0

    def as_fleet(
        self,
        picked: list[Vehicle],
        *,
        speed_kmh: float,
        service_time_min: int,
    ) -> Fleet:
        """Собирает :class:`~routeforge.solver.Fleet` из набранных машин.

        Вместимость и остаток смены передаются повекторно: в этом и смысл
        реального парка — машины разные, и вторая ходка короче первой.
        """
        return Fleet(
            count=len(picked),
            capacity=max((v.capacity for v in picked), default=0),
            max_time_min=max((v.remaining_min for v in picked), default=0),
            speed_kmh=speed_kmh,
            service_time_min=service_time_min,
            capacities=tuple(int(v.capacity) for v in picked),
            max_times_min=tuple(int(v.remaining_min) for v in picked),
        )


def vehicles_from_frame(df: pd.DataFrame, *, default_max_time_min: int) -> list[Vehicle]:
    """Строит реестр из таблицы, прочитанной :func:`routeforge.io.read_vehicles`.

    :param default_max_time_min: смена для машин, у которых она не указана —
        берётся из общих настроек расчёта.
    """
    vehicles: list[Vehicle] = []
    for _, row in df.iterrows():
        shift = row.get("max_time_min")
        depot = row.get("depot")
        vehicles.append(
            Vehicle(
                id=str(row["id"]),
                capacity=int(row["capacity"]),
                max_time_min=int(shift) if pd.notna(shift) else int(default_max_time_min),
                depot=int(depot) if pd.notna(depot) else None,
            )
        )
    return vehicles
