import pytest

from routeforge.fleet import Vehicle, VehiclePool, vehicles_from_frame
from routeforge.io import read_vehicles

import io


class UploadedFile(io.BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def pool(**kwargs) -> VehiclePool:
    return VehiclePool(
        [
            Vehicle(id="А001", capacity=4000, max_time_min=480),
            Vehicle(id="В002", capacity=6000, max_time_min=480),
            Vehicle(id="С003", capacity=3000, max_time_min=480),
        ],
        **kwargs,
    )


def test_freshest_vehicles_go_first():
    """Набирается та, у кого больше осталось: она успеет больше."""
    p = pool()
    p.spend(p.vehicles[0], 300)   # А001 осталось 180
    p.spend(p.vehicles[1], 100)   # В002 осталось 380
    picked = p.pick(demand=100_000)
    assert [v.id for v in picked] == ["С003", "В002", "А001"]


def test_pick_stops_when_capacity_covers_demand():
    """Весь парк на один кластер не забирается — остальным тоже ехать."""
    picked = pool().pick(demand=3000)
    assert len(picked) == 1
    assert picked[0].capacity >= 3000


def test_capacity_reserve_takes_one_more():
    picked = pool(capacity_reserve=5000).pick(demand=3000)
    assert len(picked) == 2


def test_spent_vehicle_stops_being_offered():
    p = pool()
    p.spend(p.vehicles[0], 480)
    assert "А001" not in [v.id for v in p.available()]


def test_remainder_below_threshold_is_zeroed():
    """Остаток меньше порога — это не «почти смена», а ноль.

    Иначе в парке копятся машины с десятью минутами в запасе, которые
    формально свободны, а на линию выйти не могут.
    """
    p = pool(min_remaining_min=40)
    p.spend(p.vehicles[0], 450)   # осталось бы 30
    assert p.vehicles[0].remaining_min == 0
    assert "А001" not in [v.id for v in p.available()]


def test_vehicle_bound_to_depot_is_offered_only_there():
    p = VehiclePool(
        [
            Vehicle(id="А001", capacity=4000, max_time_min=480, depot=0),
            Vehicle(id="В002", capacity=4000, max_time_min=480, depot=1),
            Vehicle(id="С003", capacity=4000, max_time_min=480),  # без привязки
        ]
    )
    assert [v.id for v in p.available(depot=0)] == ["А001", "С003"]
    assert [v.id for v in p.available(depot=1)] == ["В002", "С003"]


def test_empty_pool_returns_nothing_rather_than_inventing_a_vehicle():
    p = pool()
    for v in p.vehicles:
        p.spend(v, 480)
    assert p.pick(demand=1000) == []


def test_fleet_carries_per_vehicle_capacity_and_remaining_time():
    p = pool()
    p.spend(p.vehicles[0], 200)
    picked = p.pick(demand=100_000)
    fleet = p.as_fleet(picked, speed_kmh=40.0, service_time_min=10)
    assert fleet.count == 3
    assert fleet.capacity_per_vehicle == [v.capacity for v in picked]
    assert fleet.time_per_vehicle == [v.remaining_min for v in picked]


def test_registry_is_read_into_vehicles():
    content = (
        "Гос. Номер;Объем кузова;Смена;База\n"
        "А123ВС;16000;480;0\n"
        "В456ОР;20000;;\n"
    ).encode("utf-8")
    df = read_vehicles(UploadedFile(content, "reestr.csv"))
    vehicles = vehicles_from_frame(df, default_max_time_min=600)
    assert [v.id for v in vehicles] == ["А123ВС", "В456ОР"]
    assert vehicles[0].max_time_min == 480
    assert vehicles[0].depot == 0
    # Смена не указана — берётся из настроек расчёта, а не выдумывается.
    assert vehicles[1].max_time_min == 600
    assert vehicles[1].depot is None


def test_remaining_starts_at_full_shift():
    assert Vehicle(id="А", capacity=1, max_time_min=300).remaining_min == 300


def test_fleet_rejects_vectors_of_wrong_length():
    from routeforge.solver import Fleet

    with pytest.raises(ValueError, match="capacities"):
        Fleet(count=2, capacity=100, capacities=(100,))
