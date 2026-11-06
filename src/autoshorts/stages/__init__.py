"""Стадии роликов (`stages/<name>.py`, `@register_stage`), контракт — `stages/base.py` (§5.5).

`discover_stages()` импортирует все модули пакета, кроме `base`, и тем самым заполняет реестр
стадий (§10.1 п. 2: добавление стадии не трогает общих файлов). Вызывается из
`tasks.registry.load_all()` при старте воркера; при импорте пакета ничего не подгружается,
чтобы `from autoshorts.stages.base import StageContext` не тянул провайдеров и ffmpeg-обвязку.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

log = logging.getLogger(__name__)

_SKIP = frozenset({"base"})


def discover_stages(*, strict: bool = True) -> list[str]:
    """Импортировать все `autoshorts.stages.*` (кроме `base` и `_*`). Возвращает имена модулей."""
    imported: list[str] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_") or info.name in _SKIP:
            continue
        name = f"{__name__}.{info.name}"
        try:
            importlib.import_module(name)
            imported.append(name)
        except Exception:
            if strict:
                raise
            log.warning("stage module %s failed to import", name, exc_info=True)
    return imported
