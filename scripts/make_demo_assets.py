"""Строит демонстрационные материалы: картинку для README и карту folium.

    python scripts/make_demo_assets.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from routeforge.config import Settings
from routeforge.io import read_points
from routeforge.pipeline import plan_routes_sync
from routeforge.polylines import straight_polylines
from routeforge.viz import PALETTE, plot_depots, plot_routes, plot_sites, save_map

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    sites = read_points(ROOT / "data/sample/sites.csv")
    depots_df = pd.read_csv(ROOT / "data/sample/depots.csv")
    depots = list(depots_df[["lat", "lon"]].itertuples(index=False, name=None))

    result = plan_routes_sync(
        sites,
        depots,
        settings=Settings(
            distance_method="haversine",
            max_sites_per_cluster=60,
            vehicle_capacity=4000,
            solver_time_limit_s=5,
        ),
        depot_capacities=list(depots_df["capacity"]),
    )

    # Ломаные маршрутов в координатах исходных точек.
    all_lines: list[list[tuple[float, float]]] = []
    for (depot_id, cluster_id), solution in sorted(result.solutions.items()):
        chunk = result.sites[
            (result.sites["depot"] == depot_id) & (result.sites["cluster"] == cluster_id)
        ]
        nodes = [depots[depot_id], *chunk[["lat", "lon"]].itertuples(index=False, name=None)]
        all_lines.extend(straight_polylines([r.nodes for r in solution.routes], nodes))

    _png(result, depots, all_lines)
    _html(result, depots, all_lines)


def _png(result, depots, lines) -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(13, 5.6), dpi=140)

    for depot_id, colour in zip(sorted(set(result.sites["depot"])), PALETTE):
        block = result.sites[result.sites["depot"] == depot_id]
        left.scatter(block["lon"], block["lat"], s=9, c=colour, alpha=0.75, label=f"База {depot_id}")
    left.scatter(
        [d[1] for d in depots], [d[0] for d in depots],
        s=170, c="black", marker="*", zorder=5, label="Базы",
    )
    left.set_title("Шаг 1: точки распределены по базам", fontsize=11)
    left.legend(fontsize=8, loc="upper right")

    for i, line in enumerate(lines):
        if len(line) < 2:
            continue
        right.plot([p[1] for p in line], [p[0] for p in line],
                   c=PALETTE[i % len(PALETTE)], lw=1.1, alpha=0.85)
    right.scatter(result.sites["lon"], result.sites["lat"], s=5, c="0.55", zorder=3)
    right.scatter(
        [d[1] for d in depots], [d[0] for d in depots],
        s=170, c="black", marker="*", zorder=5,
    )
    right.set_title(
        f"Шаг 2: {result.vehicles_used} маршрутов, "
        f"{result.total_distance_m / 1000:.0f} км",
        fontsize=11,
    )

    for ax in (left, right):
        ax.set_xlabel("долгота", fontsize=9)
        ax.set_ylabel("широта", fontsize=9)
        ax.grid(alpha=0.2, lw=0.5)
        ax.tick_params(labelsize=8)

    fig.tight_layout()
    out = ROOT / "docs/demo.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight")
    print(f"картинка -> {out}")


def _html(result, depots, lines) -> None:
    fmap = plot_sites(
        list(result.sites[["lat", "lon"]].itertuples(index=False, name=None)),
        labels=result.sites["depot"].tolist(),
    )
    plot_depots(depots, fmap=fmap)
    plot_routes(lines, fmap=fmap)
    out = save_map(fmap, ROOT / "data/sample/demo_map.html")
    print(f"карта -> {out}")


if __name__ == "__main__":
    main()
