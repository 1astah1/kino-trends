"""Отчёт об оригинальности клипа перед заливкой (OWNER-DECISIONS п. 5, ARCHITECTURE §1.3, §6.3.8, §6.3.9).

Цель владельца — чтобы нарезку продвигали рекомендации YouTube. YouTube не продвигает и не
монетизирует «reused content»: проверяющие смотрят, видит ли зритель заметную разницу с
оригиналом — комментарий, сюжет, объяснение, голос автора (research §2.3). Поэтому
«оригинальность» здесь — измеряемый авторский вклад, а не отличие пикселей. Маскировки чужого
контента (зеркало, сдвиг тона и скорости, шум, дрожание кропа, вычистка метаданных) в системе
нет: она не спасает от ручных жалоб и от классификатора неаутентичного контента (§1.3).

Что проверяется (`checks[].code`):

| code | что | fail / warn |
|---|---|---|
| author_share | доля авторского вклада §6.3.8 | < 0,30 при чужих правах — fail; для own только показ |
| voice_balance | доля держится на голосе, а не на надписях | голос < 0,15: чужие права — fail, иначе warn |
| own_voice | секунды голоса автора, живой он или TTS | 0 с — warn |
| own_text | секунды своего текста на экране (не цитаты) | нет заголовка — warn |
| author_text | шаблон, заглушка, пустые фразы, пересказ, запреты | запрет/гарантия/ссылка, mock — fail; |
| | | шаблон без правки: чужие права — fail, свои — warn |
| author_text_repetition | похожесть реплик на прошлые ролики канала | ≥ 0,9 — fail, ≥ 0,5 — warn |
| repeat_moment | те же предложения источника уже нарезаны (Jaccard по sid) | ≥ 0,5 — fail, ≥ 0,2 — warn |
| library_similarity | dHash против своей библиотеки (`pipeline/similarity.py`) | ≥ 0,90 — warn |
| foreign_watermark | чужой логотип в источнике (`watermark_check`) | warning — fail, hint — warn |
| rights | тип прав, срок, атрибуция, allowlist Content ID | нет/истекли — fail |

Итог `verdict`: fail, если есть хоть один fail; warn, если есть warn; иначе pass. К каждому
замечанию — конкретная подсказка по-русски («добавьте вставку-комментарий после sid 57 …»).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from autoshorts.clipping.author.share import (
    AUTHOR_SHARE_MIN,
    GATE_FOR_OWN,
    AuthorShare,
    extra_needed,
    share_gate,
)
from autoshorts.clipping.author.textutil import text_similarity

Status = Literal["pass", "warn", "fail", "info", "skip"]
REPEAT_WARN = 0.2
REPEAT_FAIL = 0.5
LIBRARY_WARN = 0.90  # settings.similarity_warn_threshold
TEXT_REPEAT_WARN = 0.5
# Почти дословно та же озвучка, что в другом ролике канала: YouTube прямо называет «одинаковую
# озвучку» признаком неаутентичного (шаблонного) контента (research §2.3) — это стоп, не совет.
TEXT_REPEAT_FAIL = 0.9
LINE_REPEAT_MIN_CHARS = 20  # короткие реплики («Да.») не сравниваем построчно
VOICE_SHARE_MIN = 0.15
_INSERT_LOST_RE = re.compile(r"^insert_(\d+)_(dropped_to_fit|skipped_out_of_range):sid(\d+)$")

CHECK_TITLES_RU = {
    "author_share": "Доля авторского вклада (author_share)",
    "voice_balance": "Голос против надписей",
    "own_voice": "Голос автора",
    "own_text": "Свой текст на экране",
    "author_text": "Качество авторского текста",
    "author_text_repetition": "Повтор своих реплик",
    "repeat_moment": "Момент уже нарезан",
    "library_similarity": "Похожесть на свою библиотеку",
    "foreign_watermark": "Чужой водяной знак",
    "rights": "Права на источник",
}
STATUS_RU = {"pass": "ок", "warn": "внимание", "fail": "стоп", "info": "справка", "skip": "не проверялось"}
VERDICT_RU = {
    "pass": "можно заливать",
    "warn": "можно заливать после правок из списка",
    "fail": "не заливать, пока не исправлено",
}
ROLE_RU = {
    "cold_open": "Хук (cold open)",
    "insert_0": "Вставка 1",
    "insert_1": "Вставка 2",
    "outro": "Вывод (outro)",
}


def _md_cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        value = f"{value:.2f}"
    elif isinstance(value, Mapping):
        value = ", ".join(f"{k} {v}" for k, v in value.items())
    return str(value).replace("|", "\\|").replace("\n", " ")


_RIGHTS_WARNING_RU = {
    "content_id_allowlist_not_confirmed": (
        "Канал не подтверждён в allowlist Content ID правообладателя — возможны клеймы."
    ),
    "author_share_gate_applies": "Чужие права: нужен авторский вклад не меньше 30%.",
    "cc_by_attribution_in_description": "CC BY: укажите атрибуцию в описании ролика.",
    "license_version_unknown": "Не указана версия лицензии CC BY.",
    "license_not_recognized_as_cc_by": "Лицензия не распознана как CC BY — проверьте вручную.",
    "public_domain_check_embedded_music": "Общественное достояние: проверьте, нет ли внутри чужой музыки.",
    "rights_expire_within_30_days": "Права истекают в ближайшие 30 дней.",
    "platform_license_mismatch": "Лицензия на площадке не совпадает с заявленной.",
    "platform_license_missing": "На площадке не указана лицензия.",
    "owner_channel_differs_from_uploader": "Канал загрузившего не совпадает с правообладателем.",
}


@dataclass(slots=True)
class OriginalityCheck:
    code: str
    status: Status
    value: Any = None
    threshold: Any = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "value": self.value,
            "threshold": self.threshold,
            "detail": self.detail,
        }


@dataclass(slots=True)
class OriginalityReport:
    verdict: Literal["pass", "warn", "fail"]
    summary: dict[str, Any]
    checks: list[OriginalityCheck]
    suggestions: list[str]
    author_share: dict[str, Any]
    rights: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def check(self, code: str) -> OriginalityCheck | None:
        return next((c for c in self.checks if c.code == code), None)

    @property
    def fails(self) -> list[str]:
        return [c.code for c in self.checks if c.status == "fail"]

    @property
    def warns(self) -> list[str]:
        return [c.code for c in self.checks if c.status == "warn"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "fails": self.fails,
            "warns": self.warns,
            "checks": [c.to_dict() for c in self.checks],
            "suggestions": list(self.suggestions),
            "author_share": self.author_share,
            "rights": self.rights,
            "notes": list(self.notes),
        }

    def to_markdown(
        self, *, heading: str = "Отчёт об оригинальности", clip: Mapping[str, Any] | None = None
    ) -> str:
        """Отчёт для человека (Markdown, по-русски): итог, авторский вклад, проверки, что сделать.

        `clip` — необязательный контекст: file, source, range, title, author_label,
        vo_parts [(role, text)], on_screen_title, qc.
        """
        s = self.summary
        clip = dict(clip or {})
        out: list[str] = [f"# {heading}", ""]
        out.append(
            f"**Итог: {STATUS_RU.get(self.verdict, self.verdict).upper()} ({self.verdict})** — "
            f"{VERDICT_RU.get(self.verdict, '')}."
        )
        out += [
            "",
            "YouTube продвигает и монетизирует нарезку, только если зритель видит заметную разницу с "
            "оригиналом: комментарий, объяснение, сюжет, голос автора. Отчёт считает этот вклад; "
            "маскировки чужого контента в системе нет.",
            "",
        ]
        ctx_rows = [
            ("Файл", clip.get("file")),
            ("Источник", clip.get("source")),
            ("Фрагмент источника", clip.get("range")),
            ("Заголовок на экране", clip.get("on_screen_title")),
            ("Авторский текст", clip.get("author_label")),
            ("QC", clip.get("qc")),
        ]
        ctx_rows = [(k, v) for k, v in ctx_rows if v not in (None, "")]
        if ctx_rows:
            out += ["## Клип", ""] + [f"- **{k}:** {v}" for k, v in ctx_rows] + [""]
        vo = [(r, t) for r, t in (clip.get("vo_parts") or []) if t]
        if vo:
            out += ["## Авторский слой (что говорит автор)", ""]
            out += [f"- **{ROLE_RU.get(r, r)}:** {t}" for r, t in vo]
            out.append("")
        gate = "обязателен (чужие права)" if s.get("gate_applies") else "только для справки (свой источник)"
        share = self.author_share or {}
        out += [
            "## Авторский вклад",
            "",
            "| Показатель | Значение |",
            "|---|---|",
            f"| author_share | **{s.get('author_share', 0):.2f}** (порог {s.get('author_share_min', 0):.2f}, "
            f"гейт {gate}) |",
            f"| Голос автора | {s.get('own_voice_sec', 0):.1f} с |",
            f"| Свой текст на экране | {s.get('own_text_sec', 0):.1f} с "
            f"(в author_share засчитано {float(share.get('text_sec', 0) or 0):.1f} с × 0,25) |",
            f"| Стоп-кадры с комментарием | {float(share.get('freeze_sec', 0) or 0):.1f} с |",
            f"| Доля голоса и стоп-кадров | {s.get('voice_share', 0):.2f} |",
            f"| Длительность ролика | {s.get('duration_sec', 0):.1f} с |",
            "",
            "## Проверки",
            "",
            "| Проверка | Статус | Значение | Порог | Комментарий |",
            "|---|---|---|---|---|",
        ]
        order = {"fail": 0, "warn": 1, "info": 2, "pass": 3, "skip": 4}
        for c in sorted(self.checks, key=lambda c: order.get(c.status, 9)):
            out.append(
                f"| {CHECK_TITLES_RU.get(c.code, c.code)} | {STATUS_RU.get(c.status, c.status)} | "
                f"{_md_cell(c.value)} | {_md_cell(c.threshold)} | {_md_cell(c.detail)} |"
            )
        out += ["", "## Что сделать до заливки", ""]
        if self.suggestions:
            out += [f"{i}. {sug}" for i, sug in enumerate(self.suggestions, 1)]
        else:
            out.append("Замечаний нет.")
        r = self.rights or {}
        if r:
            out += ["", "## Права", ""]
            labels = (
                ("rights_type", "Тип прав"),
                ("owner_name", "Правообладатель"),
                ("license", "Лицензия"),
                ("allowlist_status", "Allowlist Content ID"),
                ("valid_until", "Действуют до"),
                ("attribution_text", "Атрибуция (в описание ролика)"),
            )
            out += [f"- **{lab}:** {r[k]}" for k, lab in labels if r.get(k)]
        if self.notes:
            out += ["", "## Заметки", ""] + [f"- {n}" for n in self.notes]
        return "\n".join(out).rstrip() + "\n"

    def to_text(self) -> str:
        """Короткая сводка для консоли и Telegram."""
        icon = {"pass": "OK", "warn": "ВНИМАНИЕ", "fail": "СТОП"}[self.verdict]
        s = self.summary
        lines = [
            f"Оригинальность: {icon} ({self.verdict})",
            f"  author_share {s.get('author_share', 0):.2f} (порог {s.get('author_share_min', 0):.2f}, "
            f"гейт {'да' if s.get('gate_applies') else 'нет'}), "
            f"голос автора {s.get('own_voice_sec', 0):.1f} с, "
            f"свой текст {s.get('own_text_sec', 0):.1f} с "
            f"(засчитано {float((self.author_share or {}).get('text_sec', 0) or 0):.1f} с), "
            f"длительность {s.get('duration_sec', 0):.1f} с",
        ]
        for c in self.checks:
            if c.status in ("fail", "warn"):
                lines.append(f"  [{c.status}] {c.code}: {c.detail}")
        for sug in self.suggestions:
            lines.append(f"  → {sug}")
        return "\n".join(lines)


# ---------------------------------------------------------------------- вспомогательное


def _author_parts(value: Any) -> list[tuple[str, str]]:
    """Прошлые реплики: строка (весь текст) или [(role, text), …] / [[role, text], …]."""
    if isinstance(value, str):
        return [("all", value)]
    out: list[tuple[str, str]] = []
    for item in value or ():
        if isinstance(item, str):
            out.append(("all", item))
        elif isinstance(item, Sequence) and len(item) == 2:
            out.append((str(item[0]), str(item[1])))
    return out


def sid_jaccard(a: Iterable[int], b: Iterable[int]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _parse_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def suggest_insert_sid(
    transcript: Any, edl: Any, *, taken: Iterable[int] = (), edge_gap_sec: float = 3.0
) -> tuple[int, str] | None:
    """Где лучше поставить ещё одну вставку: конец предложения в теле клипа, дальше всего от
    уже озвученных окон автора, с предпочтением длинной паузы после предложения."""
    sids = list((edl.clip or {}).get("sids") or [])
    if transcript is None or len(sids) < 3:
        return None
    body = [v for v in edl.video if v.type == "src" and v.role == "body"]
    if not body:
        return None
    taken_set = set(taken)
    author = sorted([(v.at, v.end) for v in edl.vo] + [(f.t, f.end) for f in edl.video if f.type == "freeze"])
    best: tuple[float, int] | None = None
    for sid in sids[:-1]:
        if sid in taken_set or not (0 <= sid < len(transcript.sentences)):
            continue
        st = transcript.sentences[sid]
        t_src = transcript.words[st.w1].e
        t_out = edl.src_to_out(t_src)
        if t_out is None or t_out < body[0].t + edge_gap_sec or t_out > body[-1].end - edge_gap_sec:
            continue
        dist = min((min(abs(t_out - a), abs(t_out - b)) for a, b in author), default=edl.duration)
        nxt = transcript.words[st.w1 + 1] if st.w1 + 1 < len(transcript.words) else None
        pause = (nxt.s - transcript.words[st.w1].e) if nxt else 0.5
        terminal = st.text.rstrip().endswith((".", "?", "!", "…"))
        score = dist + 2.0 * min(pause, 1.0) + (1.5 if terminal else 0.0)
        if best is None or score > best[0]:
            best = (score, sid)
    if best is None:
        return None
    sid = best[1]
    words = transcript.sentences[sid].text.split()
    tail = " ".join(words[-5:])
    return sid, ("…" + tail) if len(words) > 5 else tail


# ---------------------------------------------------------------------- отчёт


def build_originality_report(
    *,
    share: AuthorShare,
    rights: Mapping[str, Any] | None,
    rights_warnings: Sequence[str] = (),
    watermark: Mapping[str, Any] | None = None,
    edl: Any = None,
    transcript: Any = None,
    clip_sids: Sequence[int] | None = None,
    previous_clips: Sequence[Mapping[str, Any]] = (),
    library_similarity: Sequence[tuple[Any, float]] | None = None,
    library_note: str | None = None,
    author_mode: str | None = None,
    operator_edited: bool = False,
    script_issues: Sequence[Mapping[str, Any]] = (),
    author_texts: Sequence[str] = (),
    previous_author_texts: Sequence[tuple[Any, Any]] = (),
    author_parts: Sequence[tuple[str, str]] = (),
    voice_kinds: Sequence[str] = (),
    min_share: float = AUTHOR_SHARE_MIN,
    require_voice: bool = True,
    gate_for_own: bool = GATE_FOR_OWN,
    today: date | None = None,
) -> OriginalityReport:
    """Собрать отчёт. Всё, чего нет на входе, помечается `skip`, а не считается пройденным."""
    rights = dict(rights or {})
    rt = (rights.get("rights_type") or "").strip().lower() or None
    gate = share_gate(share.value, rt, min_share=min_share, gate_for_own=gate_for_own)
    checks: list[OriginalityCheck] = []
    sugg: list[str] = []
    notes: list[str] = []
    has_script = edl is not None and bool(getattr(edl, "vo", None) or getattr(edl, "texts", None))

    # --- author_share
    if gate.applies:
        st: Status = "pass" if gate.passed else "fail"
        detail = f"{share.value:.2f} против порога {min_share:.2f} (чужие права: гейт обязателен)"
    else:
        st = "pass" if gate.passed else "info"
        detail = f"{share.value:.2f}; свой источник — значение только показывается (порог {min_share:.2f})"
    checks.append(OriginalityCheck("author_share", st, round(share.value, 3), min_share, detail))
    if not gate.passed:
        freeze_x = extra_needed(share, min_share, 1.0, extends_duration=True)
        over_x = extra_needed(share, min_share, 0.5, extends_duration=False)
        where = None
        if edl is not None and transcript is not None:
            taken = [
                i.get("after_sid") for i in ((edl.meta or {}).get("inserts") or []) if isinstance(i, Mapping)
            ]
            where = suggest_insert_sid(transcript, edl, taken=[t for t in taken if t is not None])
        if where is not None:
            sid, tail = where
            new_share = (
                (share.value * share.duration + freeze_x) / (share.duration + freeze_x)
                if freeze_x != float("inf")
                else share.value
            )
            sugg.append(
                f"Добавьте вставку-комментарий (freeze_zoom) после sid {sid} («{tail}») примерно на "
                f"{max(2.5, freeze_x):.1f} с своим голосом — author_share вырастет до "
                f"≈{max(new_share, min_share):.2f}. "
                "Скажите то, чего нет в исходнике: объяснение, контекст, контраргумент или вывод."
            )
        else:
            sugg.append(
                f"Не хватает авторского вклада: нужно ещё ≈{freeze_x:.1f} с комментария на стоп-кадре "
                f"(или ≈{over_x:.1f} с голоса поверх приглушённого исходника)."
            )
    # --- голос vs текст
    # Сигналим, если долю держат надписи (или гейт пройден при слабом голосе); мало голоса без
    # надписей — это просто низкий author_share, о нём говорит первая проверка. При чужих правах
    # это часть гейта: fail, а не совет (надписи не делают чужой ролик авторским).
    if not require_voice:
        # канал без нейроозвучки (решение владельца): голос автора не требуется, проверку пропускаем
        checks.append(
            OriginalityCheck("voice_balance", "skip", round(share.voice_share, 3), VOICE_SHARE_MIN,
                             "канал без голоса автора")
        )
    elif (
        share.duration > 0
        and share.value > 0
        and share.voice_share < VOICE_SHARE_MIN
        and (share.text_part > share.voice_share or gate.passed)
    ):
        checks.append(
            OriginalityCheck(
                "voice_balance",
                "fail" if gate.applies else "warn",
                round(share.voice_share, 3),
                VOICE_SHARE_MIN,
                f"голос и стоп-кадры дают только {share.voice_share:.2f} (нужно ≥ {VOICE_SHARE_MIN:.2f}), "
                "остальное — надписи",
            )
        )
        sugg.append(
            "Доля держится на надписях: добавьте голос автора — хук в начале или комментарий в середине."
        )
    else:
        checks.append(OriginalityCheck("voice_balance", "pass", round(share.voice_share, 3), VOICE_SHARE_MIN))

    # --- свой голос
    kinds = set(voice_kinds or ((edl.meta or {}).get("voice_kinds") if edl is not None else []) or [])
    if share.own_voice_sec <= 0.05:
        checks.append(OriginalityCheck("own_voice", "warn", 0.0, ">0", "в ролике нет голоса автора"))
        sugg.append(
            "Добавьте хук своим голосом в начале (cold open до 160 символов): зачем смотреть этот момент."
        )
    else:
        live = "operator" in kinds
        detail = f"{share.own_voice_sec:.1f} с, " + (
            "есть живой голос оператора" if live else "только синтез (TTS)"
        )
        checks.append(OriginalityCheck("own_voice", "pass", round(share.own_voice_sec, 2), ">0", detail))
        if not live and kinds:
            sugg.append(
                "Голос автора синтезирован. Запишите хотя бы cold open своим голосом: одинаковый TTS на всех "
                "роликах — признак поточного контента для YouTube."
            )
    if edl is not None:
        roles = {v.role.split("_")[0] for v in edl.vo}
        if "cold" not in roles and has_script:
            sugg.append("Нет хука автора в начале: добавьте cold open своим голосом.")
        if "outro" not in roles and has_script:
            sugg.append(
                "Нет вывода в конце: добавьте outro — вопрос зрителю или вывод, "
                "который замыкает ролик в петлю."
            )

    # --- свой текст
    titles = [t for t in (edl.texts if edl is not None else []) if t.kind == "title"]
    if edl is not None and not titles:
        checks.append(
            OriginalityCheck(
                "own_text", "warn", round(share.own_text_sec, 2), None, "нет своего заголовка на экране"
            )
        )
        sugg.append("Сформулируйте свой заголовок на экране (до 48 символов), не цитату из исходника.")
    elif titles and not any(t.counts_as_author for t in titles):
        if any(getattr(t, "note", "") == "no_words" for t in titles):
            checks.append(
                OriginalityCheck(
                    "own_text",
                    "warn",
                    round(share.own_text_sec, 2),
                    None,
                    "в заголовке нет своих слов (знаки, эмодзи, междометия)",
                )
            )
            sugg.append(
                "Заголовок без слов не засчитывается: сформулируйте мысль ролика (2–6 слов своими словами)."
            )
        else:
            checks.append(
                OriginalityCheck(
                    "own_text",
                    "warn",
                    round(share.own_text_sec, 2),
                    None,
                    "заголовок повторяет слова исходника",
                )
            )
            sugg.append(
                "Заголовок почти целиком из слов исходника — перепишите своими словами, тогда он засчитается."
            )
    else:
        checks.append(
            OriginalityCheck("own_text", "pass" if edl is not None else "skip", round(share.own_text_sec, 2))
        )

    # --- авторский текст: запреты и гарантии, заглушка, шаблон, пустые фразы.
    # Ошибки линтера (запрещённая фраза ниши, обещание лечения или результата, ссылка на чужой
    # ресурс) — стоп в ЛЮБОМ режиме: шаблонный черновик после сбоя LLM копирует заголовок
    # кандидата и может принести такую фразу.
    mode = (author_mode or (edl.meta or {}).get("author_mode") if edl is not None else author_mode) or None
    issues = [
        i
        for i in script_issues
        if i.get("severity") in ("warn", "error") and i.get("code") != "template_text"
    ]
    errors = [i for i in issues if i.get("severity") == "error"]
    template = mode in ("mock", "heuristic") and not operator_edited
    if errors:
        checks.append(
            OriginalityCheck(
                "author_text", "fail", mode, None, "; ".join(str(i.get("detail")) for i in errors)
            )
        )
        sugg.append(
            "Уберите из авторского текста: "
            + "; ".join(str(i.get("detail")) for i in errors)
            + ". Обещания лечения и результата, запрещённые фразы ниши и ссылки на чужие ресурсы "
            "не публикуем."
        )
    elif mode == "mock" and not operator_edited:
        checks.append(
            OriginalityCheck(
                "author_text", "fail", mode, None, "текст автора — заглушка mock, публиковать нельзя"
            )
        )
    elif mode == "heuristic" and not operator_edited:
        # Чужие права: шаблон — не авторский слой (OWNER-DECISIONS п. 5), это стоп, а не совет.
        checks.append(
            OriginalityCheck(
                "author_text",
                "fail" if gate.applies else "warn",
                mode,
                None,
                "шаблонный черновик без LLM, не отредактирован"
                + (" — для чужих прав нужен свой текст" if gate.applies else ""),
            )
        )
    elif issues:
        checks.append(
            OriginalityCheck(
                "author_text", "warn", mode, None, "; ".join(str(i.get("detail")) for i in issues[:4])
            )
        )
    else:
        checks.append(OriginalityCheck("author_text", "pass" if mode else "skip", mode))
    if template and mode == "mock":
        sugg.append(
            "Сгенерируйте авторский текст через LLM или напишите его сами — сейчас это тестовая заглушка."
        )
    elif template:
        sugg.append(
            "Перепишите шаблонные реплики своими словами: шаблон на всех роликах — это «generic content»."
        )
    if not template:
        for i in issues:
            if i.get("code") == "filler":
                sugg.append(
                    f"Уберите пустые фразы в «{i.get('where')}»: {i.get('detail')}. "
                    "Замените их мыслью или фактом."
                )
            elif i.get("code") == "retells_source":
                sugg.append(
                    f"Реплика «{i.get('where')}» пересказывает исходник — добавьте своё: "
                    "почему это важно, где подвох."
                )

    # --- вставки, которые не встали в ролик (не влезли в 58 с или вне диапазона)
    if edl is not None:
        for w in getattr(edl, "warnings", None) or []:
            m = _INSERT_LOST_RE.match(str(w))
            if not m:
                continue
            role = f"insert_{m.group(1)}"
            if m.group(2) == "dropped_to_fit":
                sugg.append(
                    f"{ROLE_RU.get(role, role)} (после sid {m.group(3)}) не влезла в 58 с и снята. "
                    "Сократите хук или вывод, возьмите фрагмент короче или перенесите вставку раньше."
                )
            else:
                sugg.append(
                    f"{ROLE_RU.get(role, role)} (после sid {m.group(3)}) не попала в ролик: слишком близко "
                    "к началу или концу фрагмента. Перенесите её в середину."
                )

    # --- повтор своих реплик: весь текст целиком и отдельные реплики (хук, вставки).
    # Одинаковое аутро на разных роликах YouTube допускает (research §2.3), одинаковый
    # комментарий — нет.
    if not author_texts and author_parts:
        author_texts = [t for _, t in author_parts]
    if author_texts and previous_author_texts:
        mine = " ".join(author_texts)
        best_id, best = None, 0.0
        line_hits: list[tuple[str, Any, float]] = []
        my_lines = [(r, t) for r, t in author_parts if r != "outro" and len(t) >= LINE_REPEAT_MIN_CHARS]
        for pid, prev in previous_author_texts:
            prev_parts = _author_parts(prev)
            s = text_similarity(mine, " ".join(t for _, t in prev_parts))
            if s > best:
                best_id, best = pid, s
            for role, text in my_lines:
                for prole, ptext in prev_parts:
                    if prole == "outro" or len(ptext) < LINE_REPEAT_MIN_CHARS:
                        continue
                    ls = text_similarity(text, ptext)
                    if ls >= TEXT_REPEAT_FAIL and not any(h[0] == role for h in line_hits):
                        line_hits.append((role, pid, ls))
        st2: Status = (
            "fail"
            if best >= TEXT_REPEAT_FAIL
            else "warn"
            if best >= TEXT_REPEAT_WARN or line_hits
            else "pass"
        )
        detail = f"ближе всего к ролику {best_id}" if best_id is not None else ""
        if line_hits:
            detail += "; слово в слово: " + ", ".join(
                f"{ROLE_RU.get(r, r).lower()} = ролик {pid}" for r, pid, _ in line_hits
            )
        checks.append(
            OriginalityCheck(
                "author_text_repetition",
                st2,
                round(best, 3),
                {"warn": TEXT_REPEAT_WARN, "fail": TEXT_REPEAT_FAIL},
                detail,
            )
        )
        if line_hits and st2 == "warn" and best < TEXT_REPEAT_WARN:
            r, pid, _ = line_hits[0]
            sugg.append(
                f"Реплика «{ROLE_RU.get(r, r)}» дословно повторяет ролик {pid}: одинаковый комментарий "
                "на разных роликах — признак шаблона. Скажите здесь то, что относится именно к этому моменту."
            )
        if st2 == "fail":
            sugg.append(
                f"Озвучка почти дословно повторяет ролик {best_id} ({best:.0%}): одинаковая озвучка на "
                "разных роликах — прямой признак шаблонного контента для YouTube. Напишите реплики "
                "заново под этот момент."
            )
        elif st2 == "warn" and best >= TEXT_REPEAT_WARN:
            sugg.append(
                f"Реплики автора на {best:.0%} совпадают с роликом {best_id}: "
                "одинаковые формулировки на разных "
                "роликах — признак шаблонного контента. Сформулируйте иначе."
            )
    else:
        checks.append(
            OriginalityCheck("author_text_repetition", "skip", detail="нет прошлых реплик для сравнения")
        )

    # --- тот же момент уже нарезан
    sids = list(clip_sids or ((edl.clip or {}).get("sids") if edl is not None else []) or [])
    if sids and previous_clips:
        best_clip, best_j = None, 0.0
        for pc in previous_clips:
            j = sid_jaccard(sids, pc.get("sids") or [])
            if j > best_j:
                best_clip, best_j = pc.get("id") or pc.get("label"), j
        st3: Status = "fail" if best_j >= REPEAT_FAIL else "warn" if best_j >= REPEAT_WARN else "pass"
        checks.append(
            OriginalityCheck(
                "repeat_moment",
                st3,
                round(best_j, 3),
                {"warn": REPEAT_WARN, "fail": REPEAT_FAIL},
                f"пересечение предложений с клипом {best_clip}: {best_j:.0%}" if best_clip else "",
            )
        )
        if st3 != "pass":
            sugg.append(
                f"Этот момент уже нарезан в клипе {best_clip} (совпадение предложений {best_j:.0%}). "
                "Выберите другой фрагмент или сдвиньте границы; если оставляете — дайте другой угол: "
                "другой хук и другой вывод."
            )
    else:
        checks.append(
            OriginalityCheck(
                "repeat_moment",
                "skip" if not sids else "pass",
                0.0,
                detail="нет прошлых клипов этого источника" if sids else "нет sid клипа",
            )
        )

    # --- похожесть на свою библиотеку
    if library_similarity is None:
        checks.append(
            OriginalityCheck("library_similarity", "skip", detail=library_note or "библиотека не передана")
        )
    else:
        top = max(library_similarity, key=lambda x: x[1], default=(None, 0.0))
        st4: Status = "warn" if top[1] >= LIBRARY_WARN else "pass"
        checks.append(
            OriginalityCheck(
                "library_similarity",
                st4,
                round(float(top[1]), 4),
                LIBRARY_WARN,
                f"ближайший ролик {top[0]}" if top[0] is not None else "",
            )
        )
        if st4 == "warn":
            sugg.append(
                f"Картинка почти совпадает с роликом {top[0]} ({top[1]:.2f}): не штампуйте — "
                "другой визуальный угол, "
                "другой хук или стоп-кадр в другом месте."
            )

    # --- чужой водяной знак
    if watermark is None:
        checks.append(
            OriginalityCheck("foreign_watermark", "skip", detail="проверка источника не запускалась")
        )
    else:
        sev = str(watermark.get("severity") or ("warning" if watermark.get("suspect") else "ok"))
        if watermark.get("suspect") or sev == "warning":
            best = watermark.get("best") or {}
            names = ", ".join(f"{k} {v:.2f}" for k, v in sorted(best.items(), key=lambda kv: -kv[1])[:3])
            checks.append(
                OriginalityCheck("foreign_watermark", "fail", sev, 0.8, f"найден чужой логотип: {names}")
            )
            sugg.append(
                "В кадре чужой логотип: возьмите чистый мастер у правообладателя. "
                "Логотип не маскируем — чужие "
                "водяные знаки режут охват почти до нуля."
            )
        elif sev == "hint":
            corner = (watermark.get("static_overlay") or {}).get("corner")
            checks.append(
                OriginalityCheck(
                    "foreign_watermark", "warn", sev, None, f"статичный оверлей в углу {corner or ''}".strip()
                )
            )
            sugg.append("В углу кадра статичный оверлей: проверьте глазами, не чужой ли это логотип канала.")
        else:
            checks.append(OriginalityCheck("foreign_watermark", "pass", sev))

    # --- права
    rights_status, rights_detail = _rights_status(rights, rt, today or date.today())
    checks.append(OriginalityCheck("rights", rights_status, rt, None, rights_detail))
    for w in rights_warnings:
        msg = _RIGHTS_WARNING_RU.get(w, w)
        notes.append(msg)
        if w == "content_id_allowlist_not_confirmed":
            sugg.append(
                "Попросите правообладателя внести канал в allowlist Content ID — иначе ролик получит клейм."
            )
        if w == "cc_by_attribution_in_description" and rights.get("attribution_text"):
            sugg.append(f"В описание ролика: {rights['attribution_text']}")
    if rights_status == "fail":
        sugg.append("Без действующей записи о правах ролик не публикуем: оформите права на источник.")

    # --- AI-disclosure
    if (edl is not None and (edl.meta or {}).get("ai_disclosure")) or (kinds and kinds != {"operator"}):
        notes.append(
            "Голос автора синтезирован: в чек-листе публикации отметьте пункт ai_disclosure "
            "(метаданные файла уже помечены)."
        )

    verdict: Literal["pass", "warn", "fail"] = (
        "fail"
        if any(c.status == "fail" for c in checks)
        else "warn"
        if any(c.status == "warn" for c in checks)
        else "pass"
    )
    summary = {
        "author_share": round(share.value, 3),
        "author_share_min": min_share,
        "gate_applies": gate.applies,
        "gate_passed": gate.passed,
        "voice_share": round(share.voice_share, 3),
        "own_voice_sec": round(share.own_voice_sec, 2),
        "own_text_sec": round(share.own_text_sec, 2),
        "duration_sec": round(share.duration, 2),
        "rights_type": rt,
        "author_mode": mode,
    }
    # подсказки без повторов, в порядке появления
    seen: set[str] = set()
    uniq = [s for s in sugg if not (s in seen or seen.add(s))]
    return OriginalityReport(verdict, summary, checks, uniq, share.to_dict(), _rights_public(rights), notes)


def _rights_status(rights: Mapping[str, Any], rt: str | None, today: date) -> tuple[Status, str]:
    if not rt:
        return "fail", "нет записи о правах"
    if rt not in ("own", "licensed_written", "licensed_platform", "cc_by", "public_domain", "licensed"):
        return "fail", f"неизвестный тип прав {rt}"
    vu = _parse_date(rights.get("valid_until"))
    if vu is not None and vu < today:
        return "fail", f"права истекли {vu.isoformat()}"
    if rt != "own" and not (rights.get("attribution_text") or "").strip():
        return "fail", "нет текста атрибуции"
    if rt in ("licensed_written", "licensed_platform", "licensed"):
        al = (rights.get("allowlist_status") or "unknown").lower()
        if al != "allowlisted":
            return "warn", f"лицензия, allowlist Content ID: {al}"
    if vu is not None and (vu - today).days <= 30:
        return "warn", f"права истекают {vu.isoformat()}"
    return "pass", rt


def _rights_public(rights: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("rights_type", "owner_name", "allowlist_status", "attribution_text", "valid_until", "license")
    return {k: rights.get(k) for k in keys if k in rights}


def library_similarity_for(
    video: str | Path, library: Sequence[tuple[Any, Any]]
) -> tuple[list[tuple[Any, float]] | None, str | None, Any]:
    """dHash клипа против библиотеки (`pipeline/similarity.py`, если модуль есть).

    `library` — [(id, VideoFingerprint | {"dhash": [...]} | [hex...])]. Возвращает (похожести,
    заметка, отпечаток клипа). Нет модуля — (None, причина, None).
    """
    try:
        from autoshorts.pipeline import similarity as sim  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return None, f"pipeline/similarity недоступен: {type(exc).__name__}", None
    try:
        fp = sim.fingerprint_video(Path(video))
    except Exception as exc:  # noqa: BLE001
        return None, f"не удалось снять отпечаток: {type(exc).__name__}: {exc}"[:200], None
    out: list[tuple[Any, float]] = []
    for key, other in library:
        if isinstance(other, Mapping):
            other = sim.VideoFingerprint.from_json(dict(other))
        elif isinstance(other, list | tuple):
            other = sim.VideoFingerprint.from_hex(list(other))
        out.append((key, sim.similarity(fp, other)))
    return out, None, fp


__all__ = [
    "CHECK_TITLES_RU",
    "LIBRARY_WARN",
    "REPEAT_FAIL",
    "REPEAT_WARN",
    "TEXT_REPEAT_FAIL",
    "OriginalityCheck",
    "OriginalityReport",
    "build_originality_report",
    "library_similarity_for",
    "sid_jaccard",
    "suggest_insert_sid",
]
