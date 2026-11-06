"""Нормализация анимированных креативов баннера (`banner_creative_normalize`, heavy, M2-A).

Обработчик живёт в `services/banner_creatives.py` рядом с загрузкой; здесь только импорт, чтобы
`registry.load_all()` (воркер при старте) зарегистрировал его без флага `--import`.
"""

from autoshorts.services.banner_creatives import normalize_creative_task  # noqa: F401
