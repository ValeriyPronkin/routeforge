"""Первый шаг схемы «cluster-first, route-second».

Точки обслуживания сначала распределяются по базам (депо), затем крупные
группы дробятся до размера, который посилен солверу: время решения CVRP
растёт быстрее линейного, и одна группа на 5000 точек считается дольше,
чем десять по 500.

Модуль намеренно не знает ни про Streamlit, ни про конкретные имена
колонок — на вход идут массивы, на выходе метки кластеров.
"""

from __future__ import annotations

import numpy as np
from sklearn.cluster import KMeans

try:  # scikit-learn-extra ставится не всегда и ломается на новых sklearn
    from sklearn_extra.cluster import KMedoids
except Exception:  # pragma: no cover - зависит от окружения
    KMedoids = None


def kmeans_labels(coords: np.ndarray, n_clusters: int, random_state: int = 42) -> np.ndarray:
    """Метки k-means по координатам. Фиксированный ``random_state`` — чтобы
    два запуска на одних данных давали одинаковый ответ."""
    coords = np.asarray(coords, dtype=float)
    n_clusters = max(1, min(int(n_clusters), len(coords)))
    model = KMeans(n_clusters=n_clusters, random_state=random_state, n_init=10)
    return model.fit_predict(coords)


def centroids(coords: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Центры масс кластеров в порядке возрастания метки."""
    coords = np.asarray(coords, dtype=float)
    labels = np.asarray(labels)
    return np.array([coords[labels == lab].mean(axis=0) for lab in np.unique(labels)])


def optimal_k_by_cluster_size(
    coords: np.ndarray,
    max_per_cluster: int = 1000,
    random_state: int = 42,
) -> tuple[int, np.ndarray]:
    """Наименьшее ``k``, при котором в каждом кластере не больше
    ``max_per_cluster`` точек.

    Возвращает ``(k, labels)``.

    Нижняя оценка берётся сразу как ``ceil(n / max_per_cluster)`` — меньшим
    числом кластеров ограничение не выполнить в принципе, так что перебор
    начинается с неё, а не с единицы.
    """
    coords = np.asarray(coords, dtype=float)
    n = len(coords)
    if n == 0:
        return 0, np.empty(0, dtype=int)
    if max_per_cluster < 1:
        raise ValueError("max_per_cluster должен быть >= 1")

    k_min = int(np.ceil(n / max_per_cluster))
    for k in range(k_min, n + 1):
        labels = kmeans_labels(coords, k, random_state=random_state)
        if np.bincount(labels, minlength=k).max() <= max_per_cluster:
            return k, labels
    # Недостижимо: при k == n в каждом кластере одна точка.
    labels = np.arange(n)
    return n, labels


def balanced_clusters(
    coords: np.ndarray,
    min_size: int,
    max_size: int,
    *,
    max_attempts: int = 20,
    random_state: int = 0,
) -> tuple[int, np.ndarray]:
    """Кластеризация с ограничением на размер кластера снизу и сверху.

    Возвращает ``(k, labels)`` для лучшей найденной попытки.

    Точное решение такой задачи — отдельная оптимизационная постановка;
    здесь честный перебор ``k`` с оценкой того, насколько сильно результат
    нарушает границы. Число попыток ограничено: при недостижимых
    ограничениях (например ``min_size=8`` на 20 точках при ``max_size=10``)
    функция вернёт наименее плохой вариант, а не будет крутиться вечно.
    """
    coords = np.asarray(coords, dtype=float)
    n = len(coords)
    if n == 0:
        return 0, np.empty(0, dtype=int)
    if not 1 <= min_size <= max_size:
        raise ValueError("Требуется 1 <= min_size <= max_size")

    # Диапазон k, при котором ограничения хотя бы не противоречат друг другу.
    k_low = max(1, int(np.ceil(n / max_size)))
    k_high = max(k_low, min(n, n // min_size if min_size else n))
    candidates = sorted({int(k) for k in np.linspace(k_low, k_high, num=min(max_attempts, k_high - k_low + 1))})

    best: tuple[float, int, np.ndarray] | None = None
    for k in candidates:
        labels = _fit_labels(coords, k, random_state)
        sizes = np.bincount(labels, minlength=k)
        # Суммарное нарушение границ — 0 означает точное попадание.
        penalty = float(
            np.clip(min_size - sizes, 0, None).sum() + np.clip(sizes - max_size, 0, None).sum()
        )
        if best is None or penalty < best[0]:
            best = (penalty, k, labels)
        if penalty == 0:
            break

    penalty, k, labels = best  # type: ignore[misc]
    return k, labels


def _fit_labels(coords: np.ndarray, k: int, random_state: int) -> np.ndarray:
    """KMedoids, если библиотека доступна, иначе k-means.

    KMedoids устойчивее к выбросам — одиночная площадка за 50 км не утащит
    центр кластера на себя, — но тянет за собой scikit-learn-extra, который
    отстаёт от свежих версий sklearn. Отсутствие пакета не должно ронять
    расчёт.
    """
    k = max(1, min(int(k), len(coords)))
    if KMedoids is not None:
        return KMedoids(n_clusters=k, metric="euclidean", random_state=random_state).fit_predict(coords)
    return kmeans_labels(coords, k, random_state=random_state)


UNASSIGNED = -1


def assign_to_depots(
    distances: np.ndarray,
    demands: np.ndarray | None = None,
    capacities: np.ndarray | None = None,
) -> np.ndarray:
    """Распределяет точки по базам: каждая уходит на ближайшую, у которой
    хватает остатка мощности.

    :param distances: матрица ``n_sites x n_depots`` — расстояние от точки до базы.
    :param demands: спрос каждой точки (кг, м³, штуки). ``None`` — все по нулю,
        то есть мощности не ограничивают.
    :param capacities: мощность каждой базы. ``None`` — считать неограниченной.
    :returns: массив длины ``n_sites`` с индексом базы, либо
        :data:`UNASSIGNED` (``-1``) для точек, которые не приняла ни одна база.

    Точки обходятся в порядке возрастания расстояния до ближайшей базы:
    те, у кого выбор беднее всего, занимают места первыми.
    """
    distances = np.asarray(distances, dtype=float)
    if distances.ndim != 2:
        raise ValueError("distances должна быть матрицей n_sites x n_depots")
    n_sites, n_depots = distances.shape

    demands = np.zeros(n_sites) if demands is None else np.asarray(demands, dtype=float)
    if demands.shape != (n_sites,):
        raise ValueError("demands должен быть длины n_sites")

    if capacities is None:
        remaining = np.full(n_depots, np.inf)
    else:
        remaining = np.asarray(capacities, dtype=float).astype(float).copy()
        if remaining.shape != (n_depots,):
            raise ValueError("capacities должен быть длины n_depots")

    assignment = np.full(n_sites, UNASSIGNED, dtype=int)
    order = np.argsort(distances.min(axis=1), kind="stable")

    for site in order:
        for depot in np.argsort(distances[site], kind="stable"):
            if remaining[depot] >= demands[site]:
                assignment[site] = depot
                remaining[depot] -= demands[site]
                break
    return assignment
