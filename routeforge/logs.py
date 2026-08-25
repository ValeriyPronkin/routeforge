"""Журнал работы.

Один файл на всё приложение, путь предсказуемый и задаётся настройками.
Логи, оставленные во временном каталоге, никто не найдёт — а искать их
приходится ровно тогда, когда что-то сломалось.
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from .config import Settings

_configured = False


def setup_logging(settings: Settings | None = None, *, to_console: bool = True) -> Path:
    """Настраивает loguru и возвращает путь к файлу журнала.

    Повторные вызовы ничего не делают: в Streamlit скрипт перезапускается
    на каждое действие пользователя, и без этой защиты обработчики
    накапливались бы, а строки в журнале множились.
    """
    global _configured
    settings = settings or Settings()
    path = Path(settings.log_dir) / "routeforge.log"

    if _configured:
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    if to_console:
        logger.add(sys.stderr, level=settings.log_level)
    logger.add(
        path,
        level=settings.log_level,
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        enqueue=True,
    )
    _configured = True
    logger.info("Журнал: {}", path.resolve())
    return path
