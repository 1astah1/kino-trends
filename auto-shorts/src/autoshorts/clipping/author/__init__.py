"""Авторский слой клипа (docs/ARCHITECTURE.md §6.3.8): сценарий, голос, метрика вклада.

* `script` — схема `AuthorScript` (cold open, 0–2 вставки, outro, свой заголовок), генерация
  через LLM со стиль-гайдом «настоящий комментарий, не пустые фразы», проверка `lint_script`;
* `voice` — edge-tts (legacy `pipeline/voice.py`), запись оператора, mock без сети;
* `share` — `author_share` строго по формуле §6.3.8 и гейт 0,30 для чужих прав.

Импорт пакета лёгкий: модули подгружаются по месту.
"""

from autoshorts.clipping.author.share import (
    AUTHOR_SHARE_MIN,
    AuthorShare,
    ShareGate,
    TextEvent,
    VoiceEvent,
    compute_author_share,
    share_gate,
)

__all__ = [
    "AUTHOR_SHARE_MIN",
    "AuthorShare",
    "ShareGate",
    "TextEvent",
    "VoiceEvent",
    "compute_author_share",
    "share_gate",
]
