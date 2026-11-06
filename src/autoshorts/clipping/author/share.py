"""Метрика авторского вклада `author_share` (docs/ARCHITECTURE.md §6.3.8, правило 70/30).

    author_share = ( Σ author-only сегментов (freeze-вставки, cold open/outro при duck ≥ 12 дБ)
                   + 0.5 · Σ авторского голоса поверх приглушённого исходника
                   + 0.25 · Σ экранного авторского текста, не дублирующего транскрипт ) / длительность

Как читаются слагаемые (решения записаны здесь, чтобы их было видно в ревью):

* **author-only** — окна, где зритель видит и слышит только автора: все freeze-окна (кадр
  исходника стоит, звук исходника убран) и реплики cold open / outro, если исходник под ними
  приглушён не меньше чем на 12 дБ (`AUTHOR_ONLY_DUCK_DB`);
* **голос поверх исходника (×0,5)** — остальные реплики автора, где исходник звучит тише, но
  идёт (вставки `over_ducked`, cold open / outro с приглушением меньше 12 дБ);
* **экранный текст (×0,25)** — свой заголовок и тезисы на экране, если они не пересказ
  транскрипта и в них есть слова (это проверяет сборщик EDL и ставит `counts_as_author`:
  «!!!», эмодзи или «Это так» авторским текстом не считаются);
* **заголовок засчитывается только на время прочтения** — первые `TITLE_CREDIT_SEC` (4 с) каждого
  показа. Заголовок, который висит весь ролик, не добавляет зрителю нового смысла каждую
  секунду; по букве формулы §6.3.8 он один давал бы ≈ 0,25, и гейт 0,30 проходил бы с 2–3 с голоса;
* **вклад текста ограничен сверху** `TEXT_PART_MAX` (0,10): доля держится на голосе и
  стоп-кадрах автора, надписи только добавляют. При пороге 0,30 голос и freeze обязаны дать
  не меньше 0,20;
* **каждая секунда засчитывается один раз, по наибольшему весу**: секунда freeze-вставки с
  тезисом на экране — это 1,0, а не 1,25. Так метрика не превышает 1 и её нельзя «накрутить»
  наложением текста на уже засчитанные окна.

`own_text_sec` — сколько секунд свой текст реально на экране (для отчёта), `text_sec` — сколько
из них засчитано в метрику после всех правил выше.

Гейт (§6.3.8): при `rights_type != 'own'` нужно `author_share ≥ clip_author_share_min` (0,30),
иначе `qc_failed` с `author_share`. Для `own` значение только показывается
(`clip_author_share_gate_for_own = false`). Это внутренний прокси трансформативности, а не
гарантия площадки.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from autoshorts.clipping import intervals as iv

AUTHOR_ONLY_DUCK_DB = 12.0
W_AUTHOR_ONLY = 1.0
W_VOICE_OVER = 0.5
W_TEXT = 0.25
AUTHOR_SHARE_MIN = 0.30  # settings.clip_author_share_min
GATE_FOR_OWN = False  # settings.clip_author_share_gate_for_own
AUTHOR_ONLY_ROLES = frozenset({"cold_open", "outro"})
TITLE_CREDIT_SEC = 4.0  # сколько секунд показа заголовка идёт в метрику (= LayoutConfig.title_intro_sec)
TEXT_PART_MAX = 0.10  # потолок вклада экранного текста в author_share


@dataclass(frozen=True, slots=True)
class VoiceEvent:
    """Реплика автора на выходной шкале. `duck_db` — насколько приглушён исходник (положительное число)."""

    start: float
    end: float
    role: str  # cold_open | insert | outro
    duck_db: float = 0.0
    over_freeze: bool = False


@dataclass(frozen=True, slots=True)
class TextEvent:
    start: float
    end: float
    kind: str  # title | on_screen
    counts_as_author: bool = True


@dataclass(slots=True)
class AuthorShare:
    value: float
    duration: float
    author_only_sec: float
    voice_over_sec: float
    text_sec: float
    own_voice_sec: float  # вся озвучка автора (объединение окон)
    own_text_sec: float  # весь засчитываемый экранный текст (объединение, до вычитания)
    freeze_sec: float
    intervals: dict[str, list[list[float]]] = field(default_factory=dict)
    text_capped: bool = False  # вклад текста упёрся в TEXT_PART_MAX

    @property
    def voice_share(self) -> float:
        """Доля только от голоса и freeze (без текста) — видно, держится ли метрика на надписях."""
        if self.duration <= 0:
            return 0.0
        return (W_AUTHOR_ONLY * self.author_only_sec + W_VOICE_OVER * self.voice_over_sec) / self.duration

    @property
    def text_part(self) -> float:
        return W_TEXT * self.text_sec / self.duration if self.duration > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": round(self.value, 3),
            "duration_sec": round(self.duration, 3),
            "author_only_sec": round(self.author_only_sec, 3),
            "voice_over_sec": round(self.voice_over_sec, 3),
            "text_sec": round(self.text_sec, 3),
            "own_voice_sec": round(self.own_voice_sec, 3),
            "own_text_sec": round(self.own_text_sec, 3),
            "freeze_sec": round(self.freeze_sec, 3),
            "voice_share": round(self.voice_share, 3),
            "text_part": round(self.text_part, 3),
            "text_capped": self.text_capped,
            "weights": {"author_only": W_AUTHOR_ONLY, "voice_over": W_VOICE_OVER, "text": W_TEXT},
            "intervals": self.intervals,
        }


def _first_seconds(items: Sequence[tuple[float, float]], limit: float) -> list[tuple[float, float]]:
    """Первые `limit` секунд набора отрезков (по времени)."""
    out: list[tuple[float, float]] = []
    left = max(0.0, limit)
    for a, b in iv.normalize(items):
        if left <= iv.EPS:
            break
        take = min(b - a, left)
        out.append((a, a + take))
        left -= take
    return out


def compute_author_share(
    duration: float,
    *,
    freeze_windows: Iterable[Sequence[float]] = (),
    voice: Iterable[VoiceEvent] = (),
    texts: Iterable[TextEvent] = (),
    author_only_duck_db: float = AUTHOR_ONLY_DUCK_DB,
    title_credit_sec: float = TITLE_CREDIT_SEC,
    text_part_max: float = TEXT_PART_MAX,
) -> AuthorShare:
    """author_share по окнам на выходной шкале клипа (секунды от начала ролика).

    `title_credit_sec` — сколько секунд каждого показа заголовка идёт в метрику,
    `text_part_max` — потолок вклада экранного текста (см. docstring модуля).
    """
    d = max(0.0, float(duration))
    frz = iv.clip_to(freeze_windows, 0.0, d)
    voice = list(voice)
    texts = [t for t in texts if t.counts_as_author]
    only_vo = [
        (v.start, v.end)
        for v in voice
        if v.over_freeze or (v.role in AUTHOR_ONLY_ROLES and v.duck_db >= author_only_duck_db)
    ]
    author_only = iv.clip_to(iv.union(frz, only_vo), 0.0, d)
    all_vo = iv.clip_to([(v.start, v.end) for v in voice], 0.0, d)
    voice_over = iv.subtract(all_vo, author_only)
    own_text = iv.clip_to([(t.start, t.end) for t in texts], 0.0, d)
    credited = iv.clip_to(
        [
            (t.start, min(t.end, t.start + max(0.0, title_credit_sec)))
            if t.kind == "title"
            else (t.start, t.end)
            for t in texts
        ],
        0.0,
        d,
    )
    text_rest = iv.subtract(credited, iv.union(author_only, voice_over))
    capped = False
    if d > 0 and W_TEXT > 0:
        cap_sec = max(0.0, text_part_max) * d / W_TEXT
        if iv.total(text_rest) > cap_sec + 1e-9:
            text_rest = _first_seconds(text_rest, cap_sec)
            capped = True
    a_sec, v_sec, t_sec = iv.total(author_only), iv.total(voice_over), iv.total(text_rest)
    value = 0.0
    if d > 0:
        value = (W_AUTHOR_ONLY * a_sec + W_VOICE_OVER * v_sec + W_TEXT * t_sec) / d
    return AuthorShare(
        value=min(1.0, value),
        duration=d,
        author_only_sec=a_sec,
        voice_over_sec=v_sec,
        text_sec=t_sec,
        own_voice_sec=iv.total(all_vo),
        own_text_sec=iv.total(own_text),
        freeze_sec=iv.total(frz),
        intervals={
            "author_only": iv.rounded(author_only),
            "voice_over": iv.rounded(voice_over),
            "text": iv.rounded(text_rest),
        },
        text_capped=capped,
    )


@dataclass(frozen=True, slots=True)
class ShareGate:
    applies: bool
    passed: bool
    min_share: float
    value: float
    rights_type: str | None

    @property
    def failed(self) -> bool:
        return self.applies and not self.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "applies": self.applies,
            "passed": self.passed,
            "min_share": self.min_share,
            "value": round(self.value, 3),
            "rights_type": self.rights_type,
        }


def share_gate(
    value: float,
    rights_type: str | None,
    *,
    min_share: float = AUTHOR_SHARE_MIN,
    gate_for_own: bool = GATE_FOR_OWN,
) -> ShareGate:
    """Гейт §6.3.8: для чужих прав (и неизвестных) — обязателен, для `own` — по настройке."""
    rt = (rights_type or "").strip().lower() or None
    applies = rt != "own" or gate_for_own
    return ShareGate(applies, value + 1e-9 >= min_share, float(min_share), float(value), rt)


def extra_needed(share: AuthorShare, target: float, weight: float, *, extends_duration: bool) -> float:
    """Сколько секунд вклада с весом `weight` добавить, чтобы author_share достиг `target`.

    `extends_duration=True` — freeze-вставка удлиняет ролик: (W + x)/(D + x) ≥ g ⇒ x ≥ (gD − W)/(1 − g)
    (при weight = 1). Иначе (голос поверх исходника): (W + w·x)/D ≥ g ⇒ x ≥ (gD − W)/w.
    """
    d = share.duration
    have = share.value * d
    need = target * d - have
    if need <= 0:
        return 0.0
    if extends_duration:
        denom = weight - target
        return float("inf") if denom <= 0 else need / denom
    return need / weight


__all__ = [
    "AUTHOR_ONLY_DUCK_DB",
    "AUTHOR_SHARE_MIN",
    "GATE_FOR_OWN",
    "TEXT_PART_MAX",
    "TITLE_CREDIT_SEC",
    "W_AUTHOR_ONLY",
    "W_TEXT",
    "W_VOICE_OVER",
    "AuthorShare",
    "ShareGate",
    "TextEvent",
    "VoiceEvent",
    "compute_author_share",
    "extra_needed",
    "share_gate",
]
