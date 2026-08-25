"""Корень репозитория в sys.path.

Пакет `routeforge` ставится нормально, а `services` — нет: это не библиотека,
а сервисный контур поверх неё. Чтобы тесты API находили его и при установке
пакета в CI, корень добавляется явно.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
