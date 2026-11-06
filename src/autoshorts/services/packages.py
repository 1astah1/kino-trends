"""Пакеты «К заливке»: сборка, тексты, чек-лист, выдача файла (docs/ARCHITECTURE.md §5.2, §5.4, §6.6).

Публикация ручная (OWNER-DECISIONS): инструмент готовит пакет — файл, тексты, хэштеги, закреп,
чек-лист, трекинг-код и ссылки; оператор заливает сам и вставляет ссылку (`services/publications`).

Сборка идёт в две фазы, чтобы платный вызов LLM не держал блокировки ролика:

1. `gather` + `draft_texts` — чтение и черновик заголовка и описания: LLM (OpenAI-совместимый
   провайдер, `LLM_BASE_URL` по умолчанию https://umka.chat/v1) или шаблон без LLM
   (`texts_mode=template`, UI подписывает «шаблон без LLM — проверьте тексты»);
2. `write_package` — в транзакции: пакетная ссылка (код, зарезервированный рендером, или новый),
   сборка описания, закрепа, хэштегов, чек-листа, прежний живой пакет → `superseded`.

Описание собирается из черновика и обязательных строк:
* CTA варианта баннера (`description_cta_template`) или «Подробнее: u.<домен>/<код> (ссылка в шапке
  канала)»; у продукта без `redirect_host` — просто «Ссылка — в шапке канала», без адреса;
* нарезка с чужими правами — атрибуция источника (`source_rights.attribution_text`, для CC BY
  обязательна: автор, название, источник, лицензия) и строка «Изменения: …» (что сделано с оригиналом);
* маркировка рекламы (38-ФЗ), если вариант её требует: «Реклама. <рекламодатель>. erid: <erid>».

Чек-лист: файл, аккаунт, тексты, хэштеги, Related video (YouTube), закреп, AI-метка (обязательна
при синтетическом голосе/картинках), «Реклама · erid» (при маркировке), статус allowlist Content ID
(лицензированный источник), публикация. Пункты `ad_marking` и `content_id` хранятся всегда, а в
ответ API попадают, когда их коды есть в `domain.enums.ChecklistCode` (запрос ведущему); до этого
они видны в тексте пакета (`/text`, zip).

Нарезка с чужими правами, чей отчёт об оригинальности провален (гейт author_share и др.), пакет
не получает: 409 `package_not_ready` с `details.reason=originality_fail` — «не заливать, пока не
исправлено». Отчёт, снятый при других правах (свой → чужой после QC) или без гейта, —
409 `package_not_ready` с `details.reason=originality_stale` (нужен повторный QC). Истёкшие права —
422 `rights_rejected`. Вариант с обязательной маркировкой без erid — 409 `package_not_ready`
(`details.reason=erid_missing`). Строку «Реклама… erid: …» из описания удалить нельзя
(422 `erid_required`), как и атрибуцию.
"""

from __future__ import annotations

import io
import logging
import os
import random
import re
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from autoshorts.api.schemas.packages import HASHTAG_PATTERN, PLATFORM_TEXT_LIMITS
from autoshorts.core.errors import AppError, Conflict, NotFound, ValidationFailed
from autoshorts.db.notify import publish_event
from autoshorts.domain.enums import ChecklistCode, JobKind, JobStatus
from autoshorts.domain.events import EventType
from autoshorts.repos import packages as packages_repo
from autoshorts.services import audit
from autoshorts.services import tracking as tracking_service
from autoshorts.services.audit import Actor

log = logging.getLogger(__name__)

Row = dict[str, Any]

API_PREFIX = "/api/v1"
TEXTS_LABELS = {
    "llm": "Тексты написал LLM — проверьте перед заливкой",
    "template": "Шаблон без LLM — проверьте тексты",
    "operator": "Правка оператора",
}
CHECKLIST_LABELS = {
    "file_downloaded": "Файл скачан",
    "right_account": "Открыт нужный аккаунт канала",
    "texts_pasted": "Заголовок и описание вставлены",
    "hashtags": "Хэштеги добавлены в конец описания",
    "related_video": "Related video указан (длинный ролик темы)",
    "pinned_comment": "Комментарий опубликован и закреплён",
    "ai_disclosure": "Отмечено «Изменённый или синтетический контент» (AI-метка)",
    "ad_marking": "В кадре и описании есть «Реклама · erid»",
    "content_id": "Проверен allowlist Content ID правообладателя",
    "published": "Ролик опубликован, ссылка вставлена",
}
ALLOWLIST_RU = {
    "allowlisted": "канал в allowlist — клеймов быть не должно",
    "requested": "запрос отправлен, allowlist не подтверждён — возможен клейм",
    "unknown": "статус неизвестен — возможен клейм правообладателя",
    "not_needed": "не требуется",
}
RIGHTS_RU = {
    "own": "свой",
    "licensed_written": "письменная лицензия",
    "licensed_platform": "лицензия площадки",
    "cc_by": "CC BY",
    "public_domain": "общественное достояние",
}
LLM_ESTIMATE_USD = 0.003
LLM_TIMEOUT_SEC = 25.0
LLM_STEP = "package_texts"
TITLE_FALLBACK = "Новый ролик"
URL_RE = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(ru|com|chat|me|io|net|org)\b)", re.IGNORECASE)
ENUM_CHECKLIST_CODES = frozenset(c.value for c in ChecklistCode)


# ---------------------------------------------------------------- входные данные


@dataclass
class PackageInputs:
    job: Row
    product_id: str
    product: Row | None
    niche: Row | None
    variant: Row | None
    rights: Row | None
    candidate: Row | None
    previous: Row | None  # последний пакет ролика (живой или опубликованный)
    fmt: str = "comment"  # формат канала (channels.profile.format): comment | quiz | edit

    @property
    def platform(self) -> str:
        return str(self.job["platform"])

    @property
    def is_clip(self) -> bool:
        return self.job["kind"] == JobKind.CLIP.value

    @property
    def rights_type(self) -> str | None:
        return (self.rights or {}).get("rights_type")

    @property
    def foreign_rights(self) -> bool:
        return self.is_clip and self.rights_type not in (None, "own")

    @property
    def niche_config(self) -> dict[str, Any]:
        cfg = (self.niche or {}).get("config") or {}
        return cfg if isinstance(cfg, dict) else {}


def _candidate(conn: Any, candidate_id: int | None) -> Row | None:
    if candidate_id is None:
        return None
    return conn.execute(
        "SELECT id, title, hook_text, transcript_excerpt, hook_type FROM clip_candidates WHERE id = %(id)s",
        {"id": candidate_id},
    ).fetchone()


def _channel_fmt(conn: Any, job: Row) -> str:
    try:
        from autoshorts.services.clipping import channel_format

        return channel_format(conn, job) if job.get("kind") == JobKind.CLIP.value else "comment"
    except Exception:  # noqa: BLE001 — формат только выбирает промпт, пакет важнее
        return "comment"


def resolve_product_id(conn: Any, job: Row) -> str:
    return str(
        job.get("product_id") or job.get("default_product_id") or tracking_service.default_product_id(conn)
    )


def gather(conn: Any, job_id: int, *, for_update: bool = False) -> PackageInputs:
    job = packages_repo.job_for_package(conn, job_id, for_update=for_update)
    if job is None or job.get("deleted_at") is not None:
        raise NotFound("job", "Ролик не найден")
    product_id = resolve_product_id(conn, job)
    return PackageInputs(
        job=job,
        product_id=product_id,
        product=packages_repo.product(conn, product_id),
        niche=packages_repo.niche(conn, job.get("niche_id")),
        variant=packages_repo.variant(conn, job.get("banner_variant_id")),
        rights=packages_repo.source_rights(conn, job.get("source_id")) if job["kind"] == "clip" else None,
        candidate=_candidate(conn, job.get("clip_candidate_id")),
        previous=packages_repo.current_for_job(conn, job_id),
        fmt=_channel_fmt(conn, job),
    )


# ---------------------------------------------------------------- черновик текстов


@dataclass
class DraftTexts:
    title: str
    body: str
    mode: str  # llm | template | operator
    note: str | None = None  # почему шаблон: нет ключа, LLM не ответил, бюджет
    model: str | None = None
    cost_usd: float = 0.0
    # operator/keep: сохранённые целиком тексты прежнего пакета
    keep: dict[str, Any] | None = field(default=None)
    # хэштеги, предложенные LLM под конкретный ролик (название фильма, герой, тема)
    hashtags: list[str] = field(default_factory=list)


class PackageTextsLLM(BaseModel):
    """Ответ LLM: заголовок, суть и хэштеги под ролик; ссылки, CTA, атрибуцию добавляет система."""

    title: str = Field(max_length=90, description="Заголовок ролика по-русски, до 90 символов, без хэштегов")
    description: str = Field(
        max_length=600, description="1–3 предложения: о чём ролик и зачем досмотреть; без ссылок"
    )
    hashtags: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="3–6 хэштегов вида #слово под этот ролик: название фильма или шоу, герой, тема, жанр",
    )


def clean_hashtags(raw: Any, limit: int = 6) -> list[str]:
    """Хэштеги от LLM к виду `#слово`: без пробелов и двойных решёток, без повторов, не длиннее 60."""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        tag = "#" + re.sub(r"[\s#]+", "", str(item or "")).strip("_-.,:;!?")
        if len(tag) < 3 or not re.fullmatch(HASHTAG_PATTERN, tag):
            continue
        if URL_RE.search(tag):
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= limit:
            break
    return out


def _clean(text: Any, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) <= limit:
        return s
    cut = s[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:—- ")
    return (cut or s[: limit - 1]) + "…"


def _first_sentences(text: str, n: int = 2, limit: int = 400) -> str:
    parts = re.split(r"(?<=[.!?…])\s+", (text or "").strip())
    return _clean(" ".join(p for p in parts[:n] if p), limit)


def _author_script(job: Row) -> dict[str, Any]:
    raw = job.get("author_script") or {}
    script = raw.get("script") if isinstance(raw, dict) else None
    return script if isinstance(script, dict) else {}


def _script_payload(job: Row) -> dict[str, Any]:
    payload = job.get("stage_payload") or {}
    script = payload.get("script") if isinstance(payload, dict) else None
    return script if isinstance(script, dict) else {}


def template_texts(inp: PackageInputs, *, note: str | None = None) -> DraftTexts:
    """Тексты без LLM из того, что уже есть у ролика (детерминированно)."""
    job = inp.job
    limit = PLATFORM_TEXT_LIMITS[inp.platform]["title"]
    if inp.is_clip:
        script = _author_script(job)
        cand = inp.candidate or {}
        title = (
            script.get("on_screen_title")
            or cand.get("title")
            or cand.get("hook_text")
            or (inp.rights or {}).get("source_title")
            or job.get("topic")
            or TITLE_FALLBACK
        )
        cold = ((script.get("cold_open") or {}).get("vo_text") or "").strip()
        outro = ((script.get("outro") or {}).get("vo_text") or "").strip()
        body_parts = [p for p in (cold, outro) if p]
        if not body_parts and cand.get("hook_text"):
            body_parts.append(str(cand["hook_text"]))
        body = _first_sentences(" ".join(body_parts), 3, 500)
    else:
        script = _script_payload(job)
        title = script.get("title") or script.get("hook_text") or job.get("topic") or TITLE_FALLBACK
        body = _first_sentences(script.get("body_text") or script.get("voiceover_text") or "", 2, 400)
        if not body and job.get("topic"):
            body = _clean(str(job["topic"]).capitalize(), 200)
    return DraftTexts(title=_clean(title, limit), body=body, mode="template", note=note)


def _forbidden(inp: PackageInputs) -> list[str]:
    script = inp.niche_config.get("script") or {}
    phrases = script.get("forbidden_phrases") if isinstance(script, dict) else None
    return [str(p).lower() for p in (phrases or []) if str(p).strip()]


def text_problem(inp: PackageInputs, *texts: str) -> str | None:
    """Почему текст LLM нельзя брать: ссылка/домен, запрещённая фраза ниши."""
    joined = " ".join(texts)
    if URL_RE.search(joined):
        return "link_in_text"
    low = joined.lower()
    hit = next((p for p in _forbidden(inp) if p in low), None)
    if hit:
        return "forbidden_phrase"
    return None


def _llm_context(inp: PackageInputs) -> dict[str, Any]:
    job = inp.job
    niche_label = (inp.niche or {}).get("label") or job.get("niche_id")
    prompts = inp.niche_config.get("prompts") or {}
    ctx: dict[str, Any] = {
        "platform": "YouTube Shorts" if inp.platform == "youtube" else "VK Клипы",
        "niche": niche_label,
        "topic": job.get("topic"),
        "title_hint": prompts.get("title_hint") if isinstance(prompts, dict) else None,
        # откуда фрагмент (название фильма, канал): описание должно называть произведение,
        # а не «культовую военную драму»
        "source_attribution": attribution_for(inp) if inp.is_clip else None,
        "source_title": (inp.rights or {}).get("source_title") if inp.is_clip else None,
    }
    if inp.is_clip:
        script = _author_script(job)
        cand = inp.candidate or {}
        ctx.update(
            {
                "kind": "нарезка с авторским комментарием",
                "author_title": script.get("on_screen_title"),
                "author_hook": (script.get("cold_open") or {}).get("vo_text"),
                "author_inserts": [
                    i.get("vo_text") for i in (script.get("inserts") or []) if isinstance(i, dict)
                ],
                "author_outro": (script.get("outro") or {}).get("vo_text"),
                "moment_title": cand.get("title"),
                "moment_excerpt": _clean(cand.get("transcript_excerpt") or "", 1200),
            }
        )
    else:
        script = _script_payload(job)
        ctx.update(
            {
                "kind": "короткий ролик",
                "hook": script.get("hook_text"),
                "voiceover": _clean(script.get("voiceover_text") or script.get("body_text") or "", 1200),
            }
        )
    return {k: v for k, v in ctx.items() if v not in (None, "", [])}


SYSTEM_PROMPT = (
    "Ты редактор коротких вертикальных роликов. По описанию ролика напиши по-русски заголовок "
    "(до 60 символов: в плеере Shorts на телефоне видно ~40, главное — в первых 40), описание (1–3 предложения, "
    "до 600 символов) и хэштеги. Заголовок — честная интрига о содержании ролика, без кликбейта-обмана (обещание, "
    "которого нет в ролике, YouTube удаляет), без хэштегов, эмодзи и КАПСА; не по одной схеме для всех роликов. Описание — о чём ролик и "
    "почему его стоит досмотреть; если известно название фильма, сериала или шоу (поле source_attribution "
    "или moment_title), назови его в описании. Никаких ссылок, доменов, телефонов, призывов перейти "
    "куда-либо, обещаний гарантированного результата и медицинских утверждений. Не упоминай ИИ и нейросети. "
    "Хэштеги: 3–6 штук вида #слово (кириллица или латиница, без пробелов), конкретные для этого ролика — "
    "название фильма/шоу одним словом или слитно, герой, тема, жанр, год; общие теги вроде #видео и #shorts "
    "не нужны, их добавит система. Ответь JSON с полями title, description и hashtags (массив строк)."
)


# викторина «Угадай фильм» (clipping.quiz_answer): название на площадке не выдаёт ответ, ответ — в описании
QUIZ_SYSTEM_PROMPT = (
    "Ты редактор коротких вертикальных роликов. Ролик — викторина «Угадай фильм»: зритель смотрит сцену и "
    "угадывает фильм, ответ звучит в конце ролика. Напиши по-русски заголовок (до 60 символов, зацепка — в первых 40: "
    "на телефоне видно ~40), описание и "
    "хэштеги. Заголовок — вопрос-вызов зрителю с конкретной зацепкой (жанр и десятилетие, страна, чем фильм "
    "знаменит, деталь сцены: «Угадаешь боевик нулевых по одной перестрелке?»), не шаблон «Угадай фильм»; он НЕ "
    "называет фильм, его героев, актёров и режиссёра (поле answer — это ответ, в заголовок его нельзя). Описание: 1–2 предложения с подсказкой без ответа, затем "
    "пустая строка и «Ответ: «название» (год)» (описание свёрнуто, его индексирует поиск). Не повторяй одну схему "
    "заголовка от ролика к ролику. Без хэштегов, эмодзи и КАПСА в заголовке, никаких ссылок, "
    "доменов, призывов перейти куда-либо. Не упоминай ИИ и нейросети. Хэштеги: 3–5 штук вида #слово — жанр, "
    "тема, #угадайфильм; без названия фильма и имён. Ответь JSON с полями title, description и hashtags."
)


# кино-эдит (разбор рынка 01.11.2026, docs/SHORTS_STRATEGY.md): заголовок — фраза-история, как у каналов,
# которые набирают миллионы; фильм назван в описании (поиск), в заголовке — нет
EDIT_SYSTEM_PROMPT = (
    "Ты редактор кино-эдитов для YouTube Shorts: ролик — законченная сцена фильма или сериала с оригинальным "
    "дубляжом. Напиши по-русски заголовок, описание и хэштеги. Заголовок — до 60 символов, фраза-история в третьем "
    "лице с интригой, без спойлера развязки, без названия фильма и имён актёров: «Зря они забрали последнее, что он "
    "любил», «Он понял, что у неё есть суперсила», «Спустя 6 лет она нашла того, кто её предал». Эмодзи — только "
    "если он точно передаёт настроение сцены (смешно 😂, трогательно 💔, жутко 😨, круто 🔥), чаще без него; "
    "без хэштегов и КАПСА; не одна схема для всех роликов. Описание: одно-два "
    "предложения о сцене без спойлера развязки; название фильма и источник система добавит сама — не повторяй их. "
    "Никаких ссылок, доменов и призывов — их добавит система. Не упоминай ИИ. Хэштеги: "
    "#кино #фильм #эдит и 1–2 по теме (жанр или название фильма слитно). Ответь JSON с полями title, "
    "description и hashtags."
)
EDIT_TAGS = ("#кино", "#фильм", "#эдит")
# «за кадром»: здесь фильм НАЗЫВАЕТСЯ — так пишут выигрывающие ролики («Самая смешная история за кадром Гарри Поттера»)
FACTS_SYSTEM_PROMPT = (
    "Ты редактор коротких роликов «за кадром»: голос автора рассказывает удивительный факт о фильме поверх его сцены. "
    "Напиши по-русски заголовок, описание и хэштеги. Заголовок — до 60 символов, с названием фильма, интрига о факте: "
    "«Самая смешная история со съёмок «Такси»», «Почему «Матрицу» чуть не сняли без Киану», «Как обманули зрителей "
    "в «Такси 3»». Можно один эмодзи, без хэштегов и КАПСА. Описание: 1–2 предложения о факте без пересказа "
    "всего ролика; источник система добавит сама. Только факты из ролика (поля author_hook, author_inserts, author_outro), ничего не придумывай. "
    "Никаких ссылок и призывов. Не упоминай ИИ. Хэштеги: #кино #фильм #факты и 1–2 по теме. Ответь JSON с полями "
    "title, description и hashtags."
)
FACTS_TAGS = ("#кино", "#фильм", "#факты")


def _quiz(inp: PackageInputs) -> dict[str, Any] | None:
    raw = inp.job.get("author_script") or {}
    ans = raw.get("answer") if isinstance(raw, dict) and raw.get("format") == "quiz" else None
    return ans if isinstance(ans, dict) and ans.get("film") else None


def _quiz_fix(inp: PackageInputs, quiz: dict[str, Any], title: str, body: str, tags: list[str]) -> tuple[str, str, list[str]]:
    """Страховка от спойлера: название без ответа, ответ в описании, хэштеги без названия фильма."""
    from autoshorts.clipping.author.script import QUIZ_TITLES, reveals_film

    film, year = str(quiz["film"]), quiz.get("year")
    if reveals_film(title, film):
        title = _author_script(inp.job).get("on_screen_title") or QUIZ_TITLES[0]
    answer = f"Ответ: «{film}»" + (f" ({year})" if year else "")
    if not reveals_film(body, film):
        body = f"{body}\n\n{answer}".strip()
    return title, body, [t for t in tags if not reveals_film(t, film)]


def make_llm_client() -> Any:
    """OpenAI-совместимый клиент (ключ `LLM_API_KEY` из секретов или env) или None.

    `PACKAGE_LLM=none` — всегда шаблон; `PACKAGE_LLM=mock` — заглушка (не в prod)."""
    mode = (os.environ.get("PACKAGE_LLM") or "auto").strip().lower()
    if mode == "none":
        return None
    from autoshorts.clipping.llm_client import LLMClient

    if mode == "mock":
        from autoshorts.pipeline.voice import mock_allowed

        if mock_allowed():
            import json

            def responder(messages: list[dict[str, Any]], mode: str) -> str:
                return json.dumps({"title": "Тестовый заголовок", "description": "Тестовое описание ролика."})

            return LLMClient.mock(responder, model="mock-package-texts")
    key = None
    try:
        from autoshorts.services.secrets import get_secret

        key = get_secret("LLM_API_KEY")
    except Exception:  # noqa: BLE001 - нет пула или ключа шифрования
        key = os.environ.get("LLM_API_KEY")
    if not key:
        return None
    return LLMClient.from_env(env={**os.environ, "LLM_API_KEY": key}, timeout=LLM_TIMEOUT_SEC)


def llm_texts(inp: PackageInputs, client: Any, budget: Any = None) -> DraftTexts:
    """Черновик через LLM; любая проблема (сеть, схема, бюджет, запрет) — шаблон с пометкой."""
    import json

    from autoshorts.core.errors import ResourceDeferred

    reservation = None
    provider = getattr(client, "provider", "openai_compat")
    if budget is not None and not getattr(client, "is_mock", False):
        try:
            reservation = budget.reserve(LLM_ESTIMATE_USD, provider=provider)
        except ResourceDeferred:
            return template_texts(inp, note="budget_paused")
    quiz = _quiz(inp) if inp.is_clip else None
    context = _llm_context(inp)
    if quiz:
        context["answer"] = {"film": quiz["film"], "year": quiz.get("year")}
    messages = [
        {"role": "system", "content": QUIZ_SYSTEM_PROMPT if quiz else {"edit": EDIT_SYSTEM_PROMPT, "facts": FACTS_SYSTEM_PROMPT}.get(inp.fmt, SYSTEM_PROMPT)},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]
    result = None
    error: str | None = None
    failed_cost = 0.0  # неудачные вызовы тоже стоят денег (repair, схема не сошлась)
    try:
        result = client.chat_json(messages, PackageTextsLLM, schema_name="package_texts", max_tokens=700)
    except Exception as exc:  # noqa: BLE001 - пакет важнее красивого заголовка
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
        failed_cost = sum(
            float(getattr(c, "cost_usd", 0.0) or 0.0) for c in (getattr(exc, "calls", None) or [])
        )
        log.warning("package texts LLM failed for job %s: %s", inp.job["id"], error)
    if budget is not None and reservation is not None:
        try:
            cost = float(result.cost_usd) if result is not None else failed_cost
            budget.settle(
                reservation,
                cost,
                step_name=LLM_STEP,
                model=getattr(client, "model", None),
                latency_ms=result.latency_ms if result is not None else None,
                status="ok" if result is not None else "error",
                error=error,
            )
        except Exception:  # noqa: BLE001
            log.warning("package texts: generation_event not recorded", exc_info=True)
    if result is None:
        return template_texts(inp, note="llm_failed")
    value = result.value
    title = _clean(value.title, PLATFORM_TEXT_LIMITS[inp.platform]["title"])
    body = _clean(value.description, 600)
    problem = text_problem(inp, title, body)
    if problem or not title:
        return template_texts(inp, note=problem or "llm_empty")
    tags = clean_hashtags(value.hashtags)
    if inp.fmt in ("edit", "facts") and not quiz:
        tags = list(dict.fromkeys([*(EDIT_TAGS if inp.fmt == "edit" else FACTS_TAGS), *tags]))[:5]
        if int(inp.job.get("id") or 0) % 2 == 0:
            # у половины роликов заголовок без эмодзи: одинаковый «… 😱» на каждом — шаблон, который YouTube
            # не считает вкладом (docs/SHORTS_RULES.md §1)
            title = re.sub(r"[\s\u200d\ufe0f]*[\U0001F300-\U0001FAFF\u2600-\u27BF]+[\s\ufe0f]*$", "", title).strip() or title
    if quiz:
        title, body, tags = _quiz_fix(inp, quiz, title, body, tags)
        title = _clean(title, PLATFORM_TEXT_LIMITS[inp.platform]["title"])
    return DraftTexts(
        hashtags=tags,
        title=title,
        body=body,
        mode="llm",
        model=getattr(client, "model", None),
        cost_usd=float(result.cost_usd or 0.0),
    )


def keep_previous(prev: Row) -> DraftTexts:
    return DraftTexts(
        title=prev["title"],
        body="",
        mode=prev.get("texts_mode") or "template",
        keep={
            "title": prev["title"],
            "description": prev["description"],
            "hashtags": list(prev.get("hashtags") or []),
            "pinned_comment": prev.get("pinned_comment"),
        },
    )


def draft_texts(
    inp: PackageInputs, *, regenerate: bool, client_factory: Any = None, budget: Any = None
) -> DraftTexts:
    """Черновик для сборки: правки оператора и (без regenerate) прежние тексты сохраняются."""
    prev = inp.previous
    if prev is not None and not regenerate:
        return keep_previous(prev)
    factory = client_factory or make_llm_client
    try:
        client = factory()
    except Exception:  # noqa: BLE001
        log.warning("package texts: LLM client unavailable", exc_info=True)
        client = None
    if client is None:
        return template_texts(inp, note="no_llm_key")
    return llm_texts(inp, client, budget)


# ---------------------------------------------------------------- сборка описания и чек-листа


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill_template(template: str | None, values: dict[str, Any]) -> str | None:
    if not template:
        return None
    try:
        return template.format_map(_SafeDict({k: ("" if v is None else v) for k, v in values.items()}))
    except (ValueError, IndexError, AttributeError, KeyError):
        return template


def hashtags_for(inp: PackageInputs, extra: Sequence[str] = ()) -> list[str]:
    """Хэштеги: все `fixed` ниши + теги под ролик от LLM (`extra`) + `per_video` из пула ниши
    (детерминированно по video_uid), без повторов, не больше лимита площадки."""
    cfg = inp.niche_config.get("hashtags") or {}
    pool = [t for t in (cfg.get("pool") or []) if isinstance(t, str)]
    fixed = [t for t in (cfg.get("fixed") or []) if isinstance(t, str)]
    per_video = int(cfg.get("per_video") or 3) if isinstance(cfg, dict) else 3
    if not pool and not fixed:
        from autoshorts.pipeline.hashtags import _NICHE_HASHTAG_POOLS

        pool = list(_NICHE_HASHTAG_POOLS.get(str(inp.job.get("niche_id")), []))
        if not pool:
            label = str((inp.niche or {}).get("label") or inp.job.get("niche_id") or "")
            tag = "#" + re.sub(r"[\s#]+", "", label).lower()
            pool = [tag] if len(tag) > 1 else []
    rng = random.Random(str(inp.job["video_uid"]))
    # теги под ролик от LLM точнее пула ниши: когда их хватает, из пула берём один общий тег,
    # а не три случайных
    take = min(max(per_video, 0), len(pool))
    if len(extra) >= 3:
        take = min(take, 1)
    picked = rng.sample(pool, take) if pool and take else []
    out: list[str] = []
    seen: set[str] = set()
    for t in [*fixed, *extra, *picked]:
        if re.fullmatch(HASHTAG_PATTERN, t) and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[: PLATFORM_TEXT_LIMITS[inp.platform]["hashtags"]]


def rendered_voice_kinds(job: Row) -> list[str] | None:
    """Чем озвучен ФИНАЛ: `stage_payload.clip_voice.kinds` — реплики, которые ушли в рендер.

    `jobs.author_voice` меняется и после рендера (запись оператора на приёмке без переделки), поэтому
    AI-метка считается по тому, что реально в файле. None — рендер старый, данных нет.
    """
    payload = job.get("stage_payload") or {}
    if not isinstance(payload, dict):
        return None
    for key in ("clip_voice", "voice"):
        block = payload.get(key)
        if isinstance(block, dict) and isinstance(block.get("kinds"), list):
            return [str(k) for k in block["kinds"]]
    return None


def ai_disclosure_required(inp: PackageInputs) -> bool:
    """AI-метка: генерация — всегда (TTS, картинки); нарезка — если хоть одна реплика финала синтезирована."""
    if not inp.is_clip:
        return True
    job = inp.job
    kinds = rendered_voice_kinds(job)
    if kinds is not None:
        return any(k != "operator" for k in kinds)
    segments = (
        ((job.get("author_voice") or {}).get("segments") or {})
        if isinstance(job.get("author_voice"), dict)
        else {}
    )
    sources = {str((s or {}).get("source") or "") for s in segments.values() if isinstance(s, dict)}
    if sources and sources <= {"operator", "operator_recording"}:
        return False
    layer = job.get("author_layer") or {}
    voice = layer.get("voice") if isinstance(layer, dict) else None
    script = _author_script(job)
    has_vo = any(
        [
            ((script.get("cold_open") or {}).get("vo_text") or "").strip(),
            ((script.get("outro") or {}).get("vo_text") or "").strip(),
            *[(i.get("vo_text") or "").strip() for i in (script.get("inserts") or []) if isinstance(i, dict)],
        ]
    )
    if voice == "operator_recording" and not sources:
        return False  # записей ещё нет, но синтеза не будет
    return bool(has_vo or sources or voice == "tts")


def changes_line(inp: PackageInputs) -> str:
    """«Изменения: …» — что сделано с оригиналом (CC BY требует указать изменения)."""
    script = _author_script(inp.job)
    parts = ["вертикальный кадр 9:16"]
    reframe = inp.job.get("reframe_mode")
    if reframe in ("auto", "track"):
        parts[0] = "вертикальный кадр 9:16 со слежением за говорящим"
    if ((script.get("cold_open") or {}).get("vo_text") or "").strip():
        parts.append("авторское вступление голосом")
    inserts = [
        i for i in (script.get("inserts") or []) if isinstance(i, dict) and (i.get("vo_text") or "").strip()
    ]
    if inserts:
        parts.append(f"авторские комментарии ({len(inserts)})")
    if ((script.get("outro") or {}).get("vo_text") or "").strip():
        parts.append("авторский вывод")
    if (script.get("on_screen_title") or "").strip():
        parts.append("свой заголовок")
    layer = inp.job.get("author_layer") or {}
    captions = layer.get("captions") if isinstance(layer, dict) else None
    if captions in (None, "both"):
        parts.append("субтитры")
    parts.append("фрагмент сокращён")
    return "Изменения: " + ", ".join(parts) + "."


def ad_marking(inp: PackageInputs) -> dict[str, Any] | None:
    raw = (inp.variant or {}).get("ad_marking") or {}
    if not isinstance(raw, dict):
        return None
    if not (raw.get("required") or raw.get("enabled") or raw.get("erid")):
        return None
    return {"erid": raw.get("erid"), "advertiser_text": raw.get("advertiser_text")}


def ad_marking_line(marking: dict[str, Any]) -> str:
    adv = (marking.get("advertiser_text") or "").strip()
    erid = marking.get("erid") or "—"
    return f"Реклама.{' ' + adv.rstrip('.') + '.' if adv else ''} erid: {erid}"


_ANY_URL_RE = re.compile(r"(https?://[^\s)]+|www\.[^\s)]+)", re.IGNORECASE)
_YOUTUBE_URL_RE = re.compile(r"(youtube\.com|youtu\.be)/", re.IGNORECASE)
_ATTR_LEAD_RE = re.compile(
    r"^(источник|source|автор|фрагмент|из\s|фильм|мультфильм|сериал|сцена)", re.IGNORECASE
)


def sanitize_attribution(text: str | None, rights_type: str | None) -> str | None:
    """Публичная атрибуция без служебного мусора: убирает повторы «Источник:», любые ссылки, кроме
    ссылки на оригинал YouTube при CC BY (её требует лицензия), пустые скобки и двойные пробелы.
    Адрес хранилища или прямой mp4 в описание ролика попасть не может."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    while True:
        t2 = re.sub(r"^(источник|source)\s*:\s*", "", t, flags=re.IGNORECASE).strip()
        if t2 == t:
            break
        t = t2

    def keep(m: re.Match[str]) -> str:
        url = m.group(0).rstrip(".,;)")
        return url if rights_type == "cc_by" and _YOUTUBE_URL_RE.search(url) else ""

    t = _ANY_URL_RE.sub(keep, t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"\s+([,.;:)])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;:")
    return t or None


def attribution_for(inp: PackageInputs) -> str | None:
    if not inp.foreign_rights:
        return None
    return sanitize_attribution((inp.rights or {}).get("attribution_text"), inp.rights_type)


def attribution_block(inp: PackageInputs) -> str | None:
    """Строка(и) атрибуции для описания. «Изменения: …» — только при CC BY: лицензия требует указать,
    что сделано с оригиналом; при лицензированных и прочих правах это служебный чек-лист,
    зрителю он не нужен."""
    text = attribution_for(inp)
    if not text:
        return None
    line = text if _ATTR_LEAD_RE.match(text) else f"Источник: {text}"
    line = line[0].upper() + line[1:]
    if inp.rights_type == "cc_by":
        line += "\n" + changes_line(inp)
    return line


def _values(inp: PackageInputs, code: str) -> dict[str, Any]:
    host = tracking_service.public_host(inp.product)
    from autoshorts.public.landing import display_domain

    return {
        "code": code,
        "code_url": f"{host}/{code}",
        "redirect_url": tracking_service.redirect_url(inp.product, code),
        "domain": display_domain(inp.product) or host,
        "host": host,
    }


NO_REDIRECT_CTA = "Ссылка — в шапке канала"


def bot_cta(product: Row | None) -> str | None:
    """Строка со ссылкой на Telegram-бота продукта (без редиректора): «BPN — VPN без тормозов 👉 https://t.me/bot»."""
    bot = (product or {}).get("telegram_bot_username")
    if not bot:
        return None
    pid = str((product or {}).get("id") or "").strip()
    name = pid.upper() if 0 < len(pid) <= 4 else (str((product or {}).get("name") or "").strip() or bot)
    pitch = str((product or {}).get("cta_pitch") or "").strip() or "VPN без тормозов: YouTube в 4K, Telegram и всё остальное"
    return f"{name} — {pitch} 👉 https://t.me/{bot} (ссылка и в шапке канала)"


def assemble(inp: PackageInputs, draft: DraftTexts, code: str) -> dict[str, Any]:
    """Итоговые тексты пакета из черновика и обязательных строк."""
    values = _values(inp, code)
    # без публичного редиректора у продукта адрес с кодом указывал бы на dev-хост: в тексте только шапка
    public = bool((inp.product or {}).get("redirect_host"))
    limits = PLATFORM_TEXT_LIMITS[inp.platform]
    attribution = attribution_for(inp)
    if draft.keep is not None:
        texts = dict(draft.keep)
        if attribution and attribution not in (texts.get("description") or ""):
            texts["description"] = (
                (texts.get("description") or "").rstrip() + "\n\n" + (attribution_block(inp) or "")
            ).strip()
    else:
        cta = fill_template((inp.variant or {}).get("description_cta_template"), values) or (
            f"Подробнее: {values['code_url']} (ссылка в шапке канала)" if public else (bot_cta(inp.product) or NO_REDIRECT_CTA)
        )
        # ссылка на продукт — первой строкой (владелец 01.11.2026: «ссылка в описании на BPN»); в Shorts описание
        # свёрнуто, первая строка видна первой, когда его открывают
        blocks = [cta.strip(), draft.body.strip()] if bot_cta(inp.product) and not public else [draft.body.strip(), cta.strip()]
        block = attribution_block(inp)
        if block:
            blocks.append(block)
        marking = ad_marking(inp)
        if marking:
            blocks.append(ad_marking_line(marking))
        description = "\n\n".join(b for b in blocks if b)
        pinned = fill_template((inp.variant or {}).get("pinned_comment_template"), values) or (
            f"{values['code_url']} — ссылка в шапке канала" if public else NO_REDIRECT_CTA
        )
        texts = {
            "title": _clean(draft.title, limits["title"]) or TITLE_FALLBACK,
            "description": description[: limits["description"]],
            "hashtags": hashtags_for(inp, draft.hashtags),
            "pinned_comment": pinned[:10000],
        }
    texts["attribution_text"] = attribution
    texts["texts_mode"] = draft.mode
    texts["related_video_hint"] = related_video_hint(inp)
    texts["audio_note"] = audio_note(inp)
    return texts


def related_video_hint(inp: PackageInputs) -> str | None:
    if inp.platform != "youtube":
        return None
    if inp.is_clip and inp.rights_type == "own":
        origin = (inp.rights or {}).get("origin_url")
        if origin and ("youtube.com" in origin or "youtu.be" in origin):
            return f"Related video: исходный длинный ролик — {origin}"
    return "Related video: поставьте длинный ролик той же темы (Studio → ролик → Related video)"


def audio_note(inp: PackageInputs) -> str | None:
    if not inp.is_clip:
        return "Звук уже сведён: трек из библиотеки площадки не добавляйте."
    if not inp.foreign_rights:
        return "Звук из своего источника: трек из библиотеки площадки не добавляйте."
    rt = inp.rights_type or ""
    note = f"Звук из источника ({RIGHTS_RU.get(rt, rt)}): трек из библиотеки площадки не добавляйте."
    if rt in ("licensed_written", "licensed_platform"):
        status = (inp.rights or {}).get("allowlist_status") or "unknown"
        note += f" Content ID: {ALLOWLIST_RU.get(status, status)}."
    return note


def build_checklist(inp: PackageInputs, texts: dict[str, Any], *, ai_required: bool) -> list[dict[str, Any]]:
    def item(code: str, required: bool = True) -> dict[str, Any]:
        return {"code": code, "done": False, "done_at": None, "done_by": None, "required": required}

    items = [item("file_downloaded"), item("right_account"), item("texts_pasted")]
    items.append(item("hashtags", bool(texts.get("hashtags"))))
    if inp.platform == "youtube":
        items.append(item("related_video", False))
    items.append(item("pinned_comment", bool(texts.get("pinned_comment"))))
    items.append(item("ai_disclosure", ai_required))
    if ad_marking(inp):
        items.append(item("ad_marking"))
    if inp.rights_type in ("licensed_written", "licensed_platform"):
        items.append(item("content_id"))
    items.append(item("published"))
    return items


# ---------------------------------------------------------------- гейты


def originality_blockers(inp: PackageInputs) -> list[str]:
    """Провалы отчёта об оригинальности, при которых чужой источник не заливаем (OWNER-DECISIONS п. 5)."""
    if not inp.foreign_rights:
        return []
    report = inp.job.get("originality") or {}
    if not isinstance(report, dict) or inp.job.get("originality_verdict") != "fail":
        return []
    summary = report.get("summary") or {}
    if isinstance(summary, dict) and summary.get("gate_applies") is False:
        return []
    return [str(c) for c in (report.get("fails") or [])] or ["originality"]


def originality_stale(inp: PackageInputs) -> str | None:
    """Отчёт снят при других правах, чем сейчас: гейт для чужих прав мог не применяться.

    Права источника можно поправить в любой момент (`PUT /sources/{id}/rights`), а отчёт и его
    `gate_applies` замораживаются на стадии qc. Свой → чужой после QC означает, что гейт
    author_share (OWNER-DECISIONS п. 5) не проверялся: пакет не собираем до повторного QC.
    """
    if not inp.foreign_rights:
        return None
    report = inp.job.get("originality")
    if not isinstance(report, dict) or not report:
        return "originality_missing"
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    if summary.get("gate_applies") is False:
        return "originality_stale"
    rights = report.get("rights") if isinstance(report.get("rights"), dict) else {}
    recorded = summary.get("rights_type") or rights.get("rights_type")
    if recorded and str(recorded).strip().lower() != str(inp.rights_type or "").strip().lower():
        return "originality_stale"
    return None


def rights_problem(inp: PackageInputs, today: date | None = None) -> str | None:
    if not inp.is_clip:
        return None
    if inp.rights is None or not inp.rights_type:
        return "rights_missing"
    until = inp.rights.get("valid_until")
    if until is not None and until < (today or date.today()):
        return "rights_expired"
    if inp.foreign_rights and not (inp.rights.get("attribution_text") or "").strip():
        return "attribution_required"
    return None


class PackageBlocked(AppError):
    status = 409
    code = "package_not_ready"
    default_message = "Пакет собрать нельзя"


def check_ready(conn: Any, inp: PackageInputs) -> Row:
    """Ролик одобрен, финал есть, права и оригинальность в порядке → строка финального артефакта."""
    from autoshorts.repos import artifacts as artifacts_repo

    job = inp.job
    if job["status"] != JobStatus.APPROVED.value:
        raise PackageBlocked(
            "Пакет собирается только для одобренного ролика",
            details={"reason": "job_not_approved", "job_id": int(job["id"]), "status": job["status"]},
        )
    final = artifacts_repo.latest(conn, int(job["id"]), "final")
    if final is None:
        raise PackageBlocked(
            "Нет финального файла ролика", details={"reason": "no_final", "job_id": int(job["id"])}
        )
    problem = rights_problem(inp)
    if problem:
        raise AppError(
            "Права на источник не позволяют заливать ролик",
            code="rights_rejected",
            status=422,
            details={"codes": [problem], "source_id": job.get("source_id")},
        )
    raw_marking = (inp.variant or {}).get("ad_marking") or {}
    if isinstance(raw_marking, dict) and raw_marking.get("required") and not raw_marking.get("erid"):
        raise PackageBlocked(
            "Вариант баннера требует маркировку рекламы, а erid не задан (38-ФЗ ст. 18.1)",
            details={"reason": "erid_missing", "banner_variant_id": inp.job.get("banner_variant_id")},
        )
    stale = originality_stale(inp)
    if stale:
        raise PackageBlocked(
            "Отчёт об оригинальности снят при других правах на источник — отправьте ролик на доработку "
            "со стадии QC, чтобы гейт для чужих прав проверился заново",
            details={
                "reason": stale,
                "job_id": int(job["id"]),
                "rights_type": inp.rights_type,
                "hint": "rework_from_qc",
            },
        )
    blockers = originality_blockers(inp)
    if blockers:
        raise PackageBlocked(
            "Отчёт об оригинальности провален — не заливать, пока не исправлено",
            details={"reason": "originality_fail", "fails": blockers, "job_id": int(job["id"])},
        )
    return final


# ---------------------------------------------------------------- запись


def publish_counts(conn: Any) -> None:
    try:
        publish_event(conn, EventType.PACKAGES, packages_repo.counts(conn))
    except Exception:  # noqa: BLE001 - SSE не должен ронять сборку
        log.warning("packages event not published", exc_info=True)


def write_package(conn: Any, job_id: int, draft: DraftTexts, actor: Actor, *, reason: str = "build") -> Row:
    """Собрать пакет в транзакции вызывающего: 409/422, если ролик не готов (см. `check_ready`)."""
    inp = gather(conn, job_id, for_update=True)
    final = check_ready(conn, inp)
    link = tracking_service.ensure_package_link(conn, inp.job, product_id=inp.product_id)
    texts = assemble(inp, draft, link["code"])
    ai_required = ai_disclosure_required(inp)
    superseded = packages_repo.supersede_live(conn, job_id)
    row = packages_repo.insert(
        conn,
        {
            "job_id": job_id,
            "channel_id": inp.job["channel_id"],
            "platform": inp.platform,
            "status": "ready",
            "title": texts["title"],
            "description": texts["description"],
            "hashtags": texts["hashtags"],
            "pinned_comment": texts.get("pinned_comment"),
            "audio_note": texts.get("audio_note"),
            "related_video_hint": texts.get("related_video_hint"),
            "checklist": build_checklist(inp, texts, ai_required=ai_required),
            "tracking_link_id": int(link["id"]),
            "final_artifact_id": int(final["id"]),
            "texts_mode": texts["texts_mode"],
            "attribution_text": texts.get("attribution_text"),
            "ai_disclosure_required": ai_required,
        },
    )
    audit.record(
        conn,
        actor,
        f"package.{reason}",
        "package",
        int(row["id"]),
        summary=f"{inp.job['video_uid']}: пакет {link['code']} ({texts['texts_mode']})",
        diff={
            "job_id": job_id,
            "code": link["code"],
            "texts_mode": texts["texts_mode"],
            "texts_note": draft.note,
            "superseded": superseded,
        },
    )
    from autoshorts.services.alerts import resolve_alert

    resolve_alert(conn, f"package_blocked:{int(job_id)}")  # алерт `package_build` о несобранном пакете
    publish_counts(conn)
    return row


def build_for_job(
    conn_factory: Any,
    job_id: int,
    actor: Actor,
    *,
    regenerate: bool,
    budget: Any = None,
    client_factory: Any = None,
) -> Row:
    """Черновик вне транзакции (LLM не держит блокировки), затем запись в своей транзакции."""
    with conn_factory() as conn:
        inp = gather(conn, job_id)
        check_ready(conn, inp)
    draft = draft_texts(inp, regenerate=regenerate, client_factory=client_factory, budget=budget)
    with conn_factory() as conn:
        return write_package(conn, job_id, draft, actor, reason="rebuild" if inp.previous else "build")


# ---------------------------------------------------------------- чтение и ответ


def require(conn: Any, package_id: int, *, for_update: bool = False) -> Row:
    row = packages_repo.get(conn, package_id, for_update=for_update)
    if row is None:
        raise NotFound("package", "Пакет не найден")
    return row


def business_tz(conn: Any) -> str:
    from autoshorts.services.settings import get_runtime_settings

    try:
        return get_runtime_settings(conn).business_timezone
    except Exception:  # noqa: BLE001
        return "Europe/Moscow"


def file_base(row: Row, tz: str = "Europe/Moscow") -> str:
    """`<channel>_<YYYY-MM-DD>_<code>` — латинское имя файлов пакета."""
    created: datetime = row["created_at"]
    try:
        day = created.astimezone(ZoneInfo(tz)).date().isoformat()
    except Exception:  # noqa: BLE001
        day = created.astimezone(UTC).date().isoformat()
    return f"{row['channel_id']}_{day}_{row['tracking_code']}"


def account_hint(row: Row) -> str:
    platform = "YouTube (Brand Account)" if row["platform"] == "youtube" else "VK (сообщество)"
    name = row.get("channel_title") or row["channel_id"]
    extra = row.get("channel_url") or row.get("platform_channel_ref")
    return f"{platform}: {name}" + (f" — {extra}" if extra else "")


def originality_view(row: Row) -> dict[str, Any] | None:
    if row.get("job_kind") != JobKind.CLIP.value:
        return None
    rep = row.get("originality")
    verdict = row.get("originality_verdict")
    if not isinstance(rep, dict) or verdict not in ("pass", "warn", "fail"):
        return None
    summary = rep.get("summary") or {}
    share = summary.get("author_share")
    if share is None:
        share = (rep.get("author_share") or {}).get("value", 0.0)
    return {
        "verdict": verdict,
        "author_share": float(share or 0.0),
        "gate_applies": bool(summary.get("gate_applies")),
        "gate_passed": bool(summary.get("gate_passed", verdict != "fail")),
        "suggestions": [str(s) for s in (rep.get("suggestions") or [])][:20],
    }


def checklist_view(items: Any) -> list[dict[str, Any]]:
    out = []
    for it in items or []:
        if not isinstance(it, dict) or it.get("code") not in ENUM_CHECKLIST_CODES:
            continue
        out.append(
            {
                "code": it["code"],
                "done": bool(it.get("done")),
                "done_at": it.get("done_at"),
                "done_by": it.get("done_by"),
                "required": bool(it.get("required", True)),
            }
        )
    return out


def package_view(conn: Any, row: Row, *, tz: str | None = None) -> dict[str, Any]:
    from autoshorts.services import autopublish as autopublish_service

    tz = tz or business_tz(conn)
    pid = int(row["id"])
    product = tracking_service.product_for(conn, row.get("link_product_id"))
    code = row["tracking_code"]
    tg, tg_https = tracking_service.telegram_urls(product, code)
    limits = PLATFORM_TEXT_LIMITS[row["platform"]]
    pub = packages_repo.publication_for_package(conn, pid)
    base = file_base(row, tz)
    size = row.get("final_bytes")
    if size is None:
        try:
            from autoshorts.core.storage import get_layout

            size = get_layout().resolve(row["final_path"]).stat().st_size
        except (OSError, ValueError):
            size = 0
    return {
        "id": pid,
        "job_id": int(row["job_id"]),
        "video_uid": row["video_uid"],
        "kind": row["job_kind"],
        "status": row["status"],
        "channel": {
            "id": row["channel_id"],
            "display_name": row.get("channel_title") or row["channel_id"],
            "platform": row["platform"],
            "account_hint": account_hint(row),
            "platform_channel_ref": row.get("platform_channel_ref"),
        },
        "texts": {
            "title": row["title"],
            "title_max": limits["title"],
            "description": row["description"],
            "description_max": limits["description"],
            "hashtags": list(row.get("hashtags") or []),
            "pinned_comment": row.get("pinned_comment"),
            "related_video_hint": row.get("related_video_hint"),
            "audio_note": row.get("audio_note"),
            "attribution_text": row.get("attribution_text"),
            "texts_mode": row.get("texts_mode") or "template",
            "texts_label": TEXTS_LABELS.get(row.get("texts_mode") or "template", TEXTS_LABELS["template"]),
        },
        "checklist": checklist_view(row.get("checklist")),
        "tracking": {
            "code": code,
            "redirect_url": tracking_service.redirect_url(product, code),
            "telegram_url": tg,
            "telegram_https_url": tg_https,
            "site_url": tracking_service.site_url(product, code, platform=row["platform"]),
        },
        "file": {
            "download_url": f"{API_PREFIX}/packages/{pid}/video",
            "filename": f"{base}.mp4",
            "size_bytes": int(size or 0),
            "duration_sec": float(row["duration_sec"]) if row.get("duration_sec") is not None else None,
            "text_url": f"{API_PREFIX}/packages/{pid}/text",
            "zip_url": f"{API_PREFIX}/packages/{pid}/zip",
        },
        "ai_disclosure_required": bool(row.get("ai_disclosure_required")),
        "originality": originality_view(row),
        "publication": (
            {
                "id": int(pub["id"]),
                "url": pub.get("url"),
                "status": pub["status"],
                "verify_status": pub["verify_status"],
                "published_at": pub.get("published_at"),
            }
            if pub
            else None
        ),
        "downloaded_at": row.get("downloaded_at"),
        "marked_uploaded_at": row.get("marked_uploaded_at"),
        "autopublish": autopublish_service.view(row),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_packages(conn: Any, **filters: Any) -> dict[str, Any]:
    rows, total = packages_repo.list_page(conn, **filters)
    tz = business_tz(conn)
    return {
        "items": [package_view(conn, r, tz=tz) for r in rows],
        "total": total,
        "limit": filters["limit"],
        "offset": filters["offset"],
    }


def job_package(conn: Any, job_id: int) -> Row:
    row = packages_repo.current_for_job(conn, job_id)
    if row is None:
        raise NotFound("package", "У ролика ещё нет пакета")
    return row


# ---------------------------------------------------------------- правки оператора


LIVE = ("ready", "downloaded")


def _not_live(row: Row) -> Conflict:
    return Conflict(
        "Пакет уже опубликован или заменён новым",
        code="package_not_ready",
        details={"package_id": int(row["id"]), "status": row["status"], "reason": "package_not_live"},
    )


def _invalid(field_name: str, message: str, kind: str) -> ValidationFailed:
    return ValidationFailed(
        message, details={"errors": [{"loc": ["body", field_name], "msg": message, "type": kind}]}
    )


ERID_TOKEN_RE = re.compile(r"erid:\s*(\S+)", re.IGNORECASE)


def required_erid_token(conn: Any, row: Row) -> str | None:
    """«erid: <значение>», которое обязано остаться в описании пакета с маркировкой рекламы.

    Пакет собран с маркировкой — в чек-листе есть `ad_marking` (хранится всегда). Значение берётся из
    описания, каким его собрал `assemble`; если строку уже удалили — из варианта баннера ролика.
    """
    items = row.get("checklist") or []
    if not any(isinstance(i, dict) and i.get("code") == "ad_marking" for i in items):
        return None
    m = ERID_TOKEN_RE.search(row.get("description") or "")
    if m:
        return f"erid: {m.group(1).rstrip('.,;')}"
    job = packages_repo.job_for_package(conn, int(row["job_id"]))
    variant = packages_repo.variant(conn, (job or {}).get("banner_variant_id"))
    raw = (variant or {}).get("ad_marking") or {}
    erid = raw.get("erid") if isinstance(raw, dict) else None
    return f"erid: {erid}" if erid else "erid:"


def patch_texts(conn: Any, package_id: int, changes: dict[str, Any], actor: Actor) -> Row:
    row = require(conn, package_id, for_update=True)
    if row["status"] not in LIVE:
        raise _not_live(row)
    limits = PLATFORM_TEXT_LIMITS[row["platform"]]
    fields: dict[str, Any] = {}
    if changes.get("title") is not None:
        title = changes["title"].strip()
        if not title:
            raise _invalid("title", "Пустой заголовок", "string_too_short")
        if len(title) > limits["title"]:
            raise _invalid("title", f"Заголовок длиннее {limits['title']} символов", "string_too_long")
        fields["title"] = title
    if changes.get("description") is not None:
        desc = changes["description"]
        if len(desc) > limits["description"]:
            raise _invalid("description", "Описание слишком длинное", "string_too_long")
        attribution = (row.get("attribution_text") or "").strip()
        if attribution and attribution not in desc:
            raise _invalid(
                "description",
                "Атрибуция источника обязана остаться в описании (чужие права, CC BY)",
                "attribution_required",
            )
        erid_token = required_erid_token(conn, row)
        if erid_token and erid_token.lower() not in desc.lower():
            raise _invalid(
                "description",
                f"Маркировка рекламы обязана остаться в описании: «{erid_token}» (38-ФЗ)",
                "erid_required",
            )
        fields["description"] = desc
    if changes.get("hashtags") is not None:
        fields["hashtags"] = list(dict.fromkeys(changes["hashtags"]))[: limits["hashtags"]]
    if "pinned_comment" in changes:
        fields["pinned_comment"] = changes["pinned_comment"] or None
    if not fields:
        return row
    fields.update({"texts_mode": "operator", "edited_by": actor.user_id})
    after = packages_repo.update(conn, package_id, fields, allowed_statuses=LIVE)
    if after is None:
        raise _not_live(require(conn, package_id))
    audit.record(
        conn,
        actor,
        "package.edit_texts",
        "package",
        package_id,
        summary=f"тексты пакета {row['tracking_code']}",
        diff={k: True for k in fields if k not in ("texts_mode", "edited_by")},
    )
    return after


def set_checklist(conn: Any, package_id: int, code: str, done: bool, actor: Actor) -> Row:
    row = require(conn, package_id, for_update=True)
    if row["status"] == "superseded":
        raise _not_live(row)
    items = [dict(i) for i in (row.get("checklist") or []) if isinstance(i, dict)]
    target = next((i for i in items if i.get("code") == code), None)
    if target is None:
        raise _invalid("code", "Такого пункта нет в чек-листе этого пакета", "checklist_code_absent")
    target["done"] = bool(done)
    target["done_at"] = datetime.now(UTC).isoformat() if done else None
    target["done_by"] = actor.user_id if done else None
    after = packages_repo.update(conn, package_id, {"checklist": items})
    audit.record(
        conn,
        actor,
        "package.checklist",
        "package",
        package_id,
        summary=f"{code}: {'да' if done else 'нет'}",
        diff={"code": code, "done": bool(done)},
    )
    return after


def mark_uploaded(conn: Any, package_id: int, actor: Actor) -> Row:
    row = require(conn, package_id, for_update=True)
    if row["status"] not in LIVE:
        raise _not_live(row)
    fields: dict[str, Any] = {
        "status": "downloaded",
        "marked_uploaded_at": datetime.now(UTC),
        "marked_uploaded_by": actor.user_id,
    }
    if row.get("downloaded_at") is None:
        fields["downloaded_at"] = datetime.now(UTC)
    after = packages_repo.update(conn, package_id, fields, allowed_statuses=LIVE)
    if after is None:
        raise _not_live(require(conn, package_id))
    audit.record(
        conn,
        actor,
        "package.mark_uploaded",
        "package",
        package_id,
        summary=f"залит, ссылка позже: {row['tracking_code']}",
    )
    publish_counts(conn)
    return after


def tick_checklist(items: Any, code: str, user_id: int | None) -> list[dict[str, Any]]:
    out = [dict(i) for i in (items or []) if isinstance(i, dict)]
    for i in out:
        if i.get("code") == code and not i.get("done"):
            i.update({"done": True, "done_at": datetime.now(UTC).isoformat(), "done_by": user_id})
    return out


def mark_downloaded(conn: Any, package_id: int, user_id: int | None) -> Row:
    """GET видео: ready → downloaded и пункт file_downloaded."""
    row = require(conn, package_id, for_update=True)
    if row["status"] == "superseded":
        return row
    changed = packages_repo.mark_downloaded(conn, package_id)
    items = tick_checklist(row.get("checklist"), "file_downloaded", user_id)
    if items != (row.get("checklist") or []):
        packages_repo.update(conn, package_id, {"checklist": items})
    if changed:
        publish_counts(conn)
    return require(conn, package_id)


# ---------------------------------------------------------------- файлы


def text_file(conn: Any, row: Row) -> str:
    """Все тексты, ссылки и чек-лист одним файлом (UTF-8)."""
    view = package_view(conn, row)
    t = view["texts"]
    tr = view["tracking"]
    ch = view["channel"]
    lines = [
        f"Пакет #{view['id']} · ролик {view['video_uid']} · {ch['display_name']} ({ch['platform']})",
        f"Аккаунт: {view['channel']['account_hint']}",
        f"Файл: {view['file']['filename']}",
        f"Тексты: {t['texts_label']}",
        "",
        "ЗАГОЛОВОК",
        t["title"],
        "",
        "ОПИСАНИЕ",
        t["description"],
        "",
        "ХЭШТЕГИ",
        " ".join(t["hashtags"]) or "—",
        "",
        "ЗАКРЕПЛЁННЫЙ КОММЕНТАРИЙ",
        t["pinned_comment"] or "—",
        "",
        "ССЫЛКИ",
        f"Код ролика: {tr['code']}",
        f"Редирект: {tr['redirect_url']}",
    ]
    if tr["telegram_https_url"]:
        lines.append(f"Telegram: {tr['telegram_https_url']} (в приложении: {tr['telegram_url']})")
    if tr["site_url"]:
        lines.append(f"Сайт: {tr['site_url']}")
    for extra in (t.get("related_video_hint"), t.get("audio_note")):
        if extra:
            lines += ["", extra]
    lines += ["", "ЧЕК-ЛИСТ"]
    for it in row.get("checklist") or []:
        if not isinstance(it, dict):
            continue
        mark = "x" if it.get("done") else " "
        opt = "" if it.get("required", True) else " (по желанию)"
        lines.append(f"[{mark}] {CHECKLIST_LABELS.get(str(it.get('code')), str(it.get('code')))}{opt}")
    orig = view.get("originality")
    if orig:
        from autoshorts.clipping.originality import VERDICT_RU

        lines += [
            "",
            "ОРИГИНАЛЬНОСТЬ",
            f"Итог: {VERDICT_RU.get(orig['verdict'], orig['verdict'])}; "
            f"author_share {orig['author_share']:.2f}",
            *[f"— {s}" for s in orig["suggestions"]],
        ]
    return "\n".join(lines).rstrip() + "\n"


def video_relpath(row: Row, conn: Any = None) -> str:
    """Файл пакета: финальный артефакт; если его уже убрала уборка — мастер опубликованного ролика."""
    rel = str(row["final_path"])
    if conn is None:
        return rel
    from autoshorts.core.storage import get_layout

    try:
        if get_layout().resolve(rel).is_file():
            return rel
    except (OSError, ValueError):
        pass
    master = conn.execute(
        """
        SELECT master_file_path FROM publications
        WHERE job_id = %(job)s AND deleted_at IS NULL AND master_file_path <> ''
        ORDER BY id DESC LIMIT 1
        """,
        {"job": row["job_id"]},
    ).fetchone()
    return str(master["master_file_path"]) if master else rel


class _ZipSink(io.RawIOBase):
    """Поток для zipfile без seek: всё записанное забирается кусками (`drain`)."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pos = 0

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        data = bytes(b)
        self._buf += data
        self._pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return False

    def seek(self, *args: Any) -> int:
        raise OSError("not seekable")

    def flush(self) -> None:
        return None

    def drain(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out


def zip_stream(
    video_path: Path, video_name: str, text_name: str, text: str, chunk: int = 1 << 20
) -> Iterator[bytes]:
    """ZIP_STORED без временного файла: видео читается и отдаётся кусками по 1 МБ."""
    sink = _ZipSink()
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        zf.writestr(
            zipfile.ZipInfo(text_name, date_time=datetime.now().timetuple()[:6]), text.encode("utf-8")
        )
        yield sink.drain()
        info = zipfile.ZipInfo(video_name, date_time=datetime.now().timetuple()[:6])
        info.compress_type = zipfile.ZIP_STORED
        with open(video_path, "rb") as src, zf.open(info, mode="w", force_zip64=False) as dst:
            while True:
                data = src.read(chunk)
                if not data:
                    break
                dst.write(data)
                out = sink.drain()
                if out:
                    yield out
        out = sink.drain()
        if out:
            yield out
    out = sink.drain()
    if out:
        yield out


__all__ = [
    "CHECKLIST_LABELS",
    "DraftTexts",
    "PackageBlocked",
    "PackageInputs",
    "ai_disclosure_required",
    "assemble",
    "build_for_job",
    "changes_line",
    "check_ready",
    "draft_texts",
    "file_base",
    "gather",
    "hashtags_for",
    "job_package",
    "list_packages",
    "make_llm_client",
    "mark_downloaded",
    "mark_uploaded",
    "package_view",
    "patch_texts",
    "require",
    "set_checklist",
    "template_texts",
    "text_file",
    "write_package",
    "zip_stream",
]
