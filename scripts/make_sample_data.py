"""Генератор демонстрационного набора.

Настоящие данные проекта — реестры мест накопления отходов по регионам —
публиковать нельзя, поэтому демо строится синтетически: точки
раскладываются вокруг нескольких баз с реалистичным разбросом и спросом.

    python scripts/make_sample_data.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# Базы разнесены по Челябинской области — координаты произвольные,
# привязки к реальным объектам нет.
DEPOTS = [
    ("Полигон Север", 55.310, 61.480),
    ("Полигон Юг", 55.050, 61.320),
    ("Полигон Восток", 55.180, 61.760),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", type=int, default=240, help="сколько точек сгенерировать")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/sample"))
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    per_depot = args.sites // len(DEPOTS)

    rows = []
    for depot_id, (name, lat, lon) in enumerate(DEPOTS):
        count = per_depot if depot_id < len(DEPOTS) - 1 else args.sites - per_depot * (len(DEPOTS) - 1)
        # Разброс ~0.06 градуса это примерно 6 км по широте — размер
        # городского района, на котором маршруты выглядят осмысленно.
        lats = rng.normal(lat, 0.06, count)
        lons = rng.normal(lon, 0.10, count)
        # Спрос: большинство площадок мелкие, хвост тяжёлый.
        demand = np.clip(rng.lognormal(mean=4.6, sigma=0.6, size=count), 20, 2000)
        for i in range(count):
            rows.append(
                {
                    "id": f"S{len(rows):04d}",
                    "lat": round(float(lats[i]), 6),
                    "lon": round(float(lons[i]), 6),
                    "demand": int(round(demand[i])),
                    "nearest_depot_hint": name,
                }
            )

    sites = pd.DataFrame(rows)
    depots = pd.DataFrame(
        [{"id": f"D{i}", "name": n, "lat": la, "lon": lo, "capacity": 60_000} for i, (n, la, lo) in enumerate(DEPOTS)]
    )

    args.out.mkdir(parents=True, exist_ok=True)
    sites.to_csv(args.out / "sites.csv", index=False)
    depots.to_csv(args.out / "depots.csv", index=False)
    print(f"{len(sites)} точек -> {args.out / 'sites.csv'}")
    print(f"{len(depots)} баз   -> {args.out / 'depots.csv'}")
    print(f"суммарный спрос: {sites['demand'].sum():,} ед.")


if __name__ == "__main__":
    main()
