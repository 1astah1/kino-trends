"""OpenAI-совместимый чат-клиент трека нарезки (docs/ARCHITECTURE.md §6.3.3).

TODO(M2): перенести под `providers/llm` (интерфейс `LlmProvider.chat_json`, §5.5) и брать ключ
из таблицы secrets. Сейчас клиент живёт внутри `clipping`, чтобы не трогать чужие файлы.

Настройки из окружения (пустая строка = не задано):
    LLM_BASE_URL   по умолчанию https://umka.chat/v1 (OWNER-DECISIONS п. 2)
    LLM_API_KEY    без ключа `LLMClient.from_env()` вернёт None — вызывающий берёт mock или эвристику
    LLM_MODEL      по умолчанию DEFAULT_MODEL
    LLM_PRICE_IN_PER_MTOK / LLM_PRICE_OUT_PER_MTOK  цена за 1 млн токенов в USD, если провайдер
                   не возвращает стоимость в `usage` (иначе cost_usd = 0 и `cost_known=False`)

`chat_json(messages, model_cls)` — строгий JSON с деградацией:
    json_schema (strict) → json_object → plain
Режим понижается, если провайдер отверг `response_format` (HTTP 400/404/415/422 или 200 без
контента). Ответ проверяется pydantic; если он битый или не проходит схему, делается ровно
один repair-запрос (в том же режиме) с текстом ошибки. Все вызовы, включая неудачные,
попадают в `ChatJsonResult.calls` с latency, токенами и стоимостью — это полезная нагрузка
для `generation_events(step_name='highlight_select')`.

Mock: `LLMClient.mock(responder)` — тот же путь разбора и валидации, но без сети и без денег.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from autoshorts.clipping.schemas import wire_schema

try:  # сигналы очереди (§6.1); модуль M1 может ещё меняться — есть запасной вариант
    from autoshorts.core.errors import PermanentError as _PermanentBase
    from autoshorts.core.errors import RetryableError as _RetryableBase
except Exception:  # pragma: no cover - fallback, если core ещё не собран

    class _RetryableBase(Exception):  # type: ignore[no-redef]
        def __init__(self, message: str = "", *, retry_after_sec: float | None = None) -> None:
            super().__init__(message)
            self.retry_after_sec = retry_after_sec

    class _PermanentBase(Exception):  # type: ignore[no-redef]
        def __init__(self, message: str = "", *, error_class: str | None = None) -> None:
            super().__init__(message)
            self.error_class = error_class


DEFAULT_BASE_URL = "https://umka.chat/v1"
DEFAULT_MODEL = "gpt-4o-mini"
Mode = Literal["json_schema", "json_object", "plain"]
MODES: tuple[Mode, ...] = ("json_schema", "json_object", "plain")
# Коды, по которым считаем, что провайдер не понял response_format, и понижаем режим.
_DEGRADE_STATUSES = frozenset({400, 404, 415, 422})
_TRUNCATED_HINT = "ответ обрезан по max_tokens, верни короче (меньше элементов, короче why)"

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------- ошибки


class LLMError(Exception):
    """База ошибок клиента."""


class LLMHTTPError(LLMError):
    def __init__(self, status: int, body: str = "", *, retry_after_sec: float | None = None) -> None:
        super().__init__(f"LLM HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body
        self.retry_after_sec = retry_after_sec


class LLMRetryableError(_RetryableBase, LLMError):
    """Сеть, таймаут, 429, 5xx: очередь повторит задачу с backoff."""


class LLMPermanentError(_PermanentBase, LLMError):
    """401/403, неверная модель, исчерпаны режимы: повтор бессмыслен."""


class LLMSchemaError(LLMPermanentError):
    """Ответ не прошёл схему даже после repair-запроса."""

    def __init__(self, message: str, *, calls: list[CallRecord] | None = None) -> None:
        super().__init__(message, error_class="llm_schema")
        self.calls = calls or []


# ---------------------------------------------------------------------- записи о вызовах


@dataclass(slots=True)
class CallRecord:
    """Один HTTP-вызов (или вызов mock). Идёт в generation_events."""

    mode: Mode
    purpose: Literal["initial", "repair"]
    ok: bool
    latency_ms: int
    http_status: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "purpose": self.purpose,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "cost_known": self.cost_known,
            "error": self.error,
        }


@dataclass(slots=True)
class ChatResponse:
    text: str
    record: CallRecord
    raw: dict[str, Any]

    @property
    def truncated(self) -> bool:
        try:
            return self.raw["choices"][0].get("finish_reason") == "length"
        except (KeyError, IndexError, TypeError, AttributeError):
            return False


@dataclass(slots=True)
class ChatJsonResult(Generic[T]):
    """Итог `chat_json`: значение, режим, был ли repair, все вызовы и суммарные деньги и время."""

    value: T
    mode_used: Mode
    repaired: bool
    provider: str
    model: str
    calls: list[CallRecord] = field(default_factory=list)
    raw_text: str = ""

    @property
    def latency_ms(self) -> int:
        return sum(c.latency_ms for c in self.calls)

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.calls)

    def event_payload(self, step_name: str = "highlight_select") -> dict[str, Any]:
        """Строка для generation_events (провайдер, модель, токены, деньги, время, режим)."""
        return {
            "step_name": step_name,
            "provider": self.provider,
            "model": self.model,
            "status": "ok",
            "mode_used": self.mode_used,
            "repaired": self.repaired,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            # колонки generation_events: units = токены (in + out), http_status последнего вызова
            "units": self.prompt_tokens + self.completion_tokens,
            "http_status": self.calls[-1].http_status if self.calls else None,
            "calls": [c.to_dict() for c in self.calls],
        }


# ---------------------------------------------------------------------- транспорт


class ChatTransport(Protocol):
    """payload OpenAI /chat/completions → JSON ответа. Ошибки: LLMHTTPError или LLMRetryableError."""

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class HttpxTransport:
    """POST {base_url}/chat/completions через httpx (синхронно, один клиент на экземпляр)."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 90.0, client: Any = None) -> None:
        import httpx  # лениво: модуль нужен и без сети (mock)

        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._client = client or httpx.Client(timeout=timeout)
        self._httpx = httpx

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        httpx = self._httpx
        try:
            resp = self._client.post(self._url, headers=self._headers, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMRetryableError(f"LLM network error: {type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            raise LLMHTTPError(resp.status_code, resp.text, retry_after_sec=_retry_after(resp.headers))
        try:
            return resp.json()
        except ValueError as exc:
            raise LLMRetryableError(f"LLM returned non-JSON body: {resp.text[:200]}") from exc

    def close(self) -> None:
        self._client.close()


Responder = Callable[[list[dict[str, Any]], Mode], str]


class MockTransport:
    """Детерминированный транспорт: ответ строит `responder(messages, mode)`; usage считается грубо."""

    def __init__(self, responder: Responder) -> None:
        self._responder = responder
        self.requests: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        mode = _mode_of(payload)
        text = self._responder(payload["messages"], mode)
        prompt_chars = sum(len(str(m.get("content", ""))) for m in payload["messages"])
        return {
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_chars // 4, "completion_tokens": len(text) // 4, "cost": 0.0},
        }


class ScriptedTransport:
    """Для тестов: отдаёт заранее заданные ответы по очереди.

    Элемент очереди: str (контент ответа 200), dict (полный JSON ответа), int (HTTP-статус ошибки)
    или исключение (будет брошено).
    """

    def __init__(self, replies: Sequence[str | dict[str, Any] | int | BaseException]) -> None:
        self._replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        if not self._replies:
            raise AssertionError("ScriptedTransport: no more replies")
        item = self._replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, int):
            raise LLMHTTPError(item, f"scripted error {item}")
        if isinstance(item, dict):
            return item
        return {
            "choices": [{"message": {"role": "assistant", "content": item}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }


def _mode_of(payload: dict[str, Any]) -> Mode:
    rf = payload.get("response_format") or {}
    kind = rf.get("type")
    if kind == "json_schema":
        return "json_schema"
    if kind == "json_object":
        return "json_object"
    return "plain"


# ---------------------------------------------------------------------- разбор ответа

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """JSON из ответа модели: целиком, из ```json-блока или от первой `{`/`[` до парной закрывающей."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _end = decoder.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON object in response: {text[:120]!r}")


def _content_text(resp: dict[str, Any]) -> str:
    try:
        msg = resp["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""
    content = msg.get("content")
    if isinstance(content, list):  # формат «части» у некоторых провайдеров
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if content is None and isinstance(msg.get("parsed"), dict | list):
        return json.dumps(msg["parsed"], ensure_ascii=False)
    return content or ""


def _short_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:8]:
            loc = ".".join(str(x) for x in err.get("loc", ()))
            parts.append(f"{loc}: {err.get('msg')}")
        return "; ".join(parts)
    return str(exc)[:400]


# ---------------------------------------------------------------------- клиент


class LLMClient:
    """Синхронный OpenAI-совместимый клиент с `chat` и `chat_json`."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        provider: str = "openai_compat",
        transport: ChatTransport | None = None,
        timeout: float = 90.0,
        temperature: float = 0.2,
        price_in_per_mtok: float | None = None,
        price_out_per_mtok: float | None = None,
        http_client: Any = None,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.provider = provider
        self.temperature = temperature
        self.price_in = price_in_per_mtok
        self.price_out = price_out_per_mtok
        if transport is None:
            if not api_key:
                raise LLMPermanentError("LLM_API_KEY is empty", error_class="llm_no_key")
            transport = HttpxTransport(base_url, api_key, timeout=timeout, client=http_client)
        self._transport = transport
        # Режимы, которые провайдер уже отверг, — не пробуем повторно в этом экземпляре.
        self._rejected_modes: set[Mode] = set()

    # ------------------------------------------------------------ конструкторы

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, **kwargs: Any) -> LLMClient | None:
        """Клиент из окружения; без LLM_API_KEY — None (вызывающий берёт mock или эвристику)."""
        env = dict(os.environ if env is None else env)
        key = (env.get("LLM_API_KEY") or "").strip()
        if not key:
            return None

        def _price(name: str) -> float | None:
            raw = (env.get(name) or "").strip()
            return float(raw) if raw else None

        return cls(
            base_url=(env.get("LLM_BASE_URL") or "").strip() or DEFAULT_BASE_URL,
            api_key=key,
            model=(env.get("LLM_MODEL") or "").strip() or DEFAULT_MODEL,
            price_in_per_mtok=_price("LLM_PRICE_IN_PER_MTOK"),
            price_out_per_mtok=_price("LLM_PRICE_OUT_PER_MTOK"),
            **kwargs,
        )

    @classmethod
    def mock(cls, responder: Responder, *, model: str = "mock-highlights-v1") -> LLMClient:
        return cls(provider="mock", model=model, transport=MockTransport(responder))

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"

    # ------------------------------------------------------------ низкий уровень

    def _cost(self, usage: dict[str, Any], prompt_t: int, completion_t: int) -> tuple[float, bool]:
        for key in ("cost", "total_cost", "cost_usd"):
            val = usage.get(key)
            if isinstance(val, int | float) and not isinstance(val, bool):
                return float(val), True
        if self.provider == "mock":
            return 0.0, True
        if self.price_in is not None and self.price_out is not None:
            return (prompt_t * self.price_in + completion_t * self.price_out) / 1_000_000, True
        return 0.0, False

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        mode: Mode = "plain",
        schema: dict[str, Any] | None = None,
        schema_name: str = "response",
        max_tokens: int = 2000,
        purpose: Literal["initial", "repair"] = "initial",
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        if mode == "json_schema":
            if schema is None:
                raise ValueError("json_schema mode needs schema")
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            }
        elif mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        try:
            resp = self._transport(payload)
        except LLMHTTPError as exc:
            rec = CallRecord(mode, purpose, False, _ms(t0), http_status=exc.status, error=str(exc)[:300])
            exc.record = rec  # type: ignore[attr-defined]
            raise
        except LLMRetryableError as exc:
            rec = CallRecord(mode, purpose, False, _ms(t0), error=str(exc)[:300])
            exc.record = rec  # type: ignore[attr-defined]
            raise
        usage = resp.get("usage") or {}
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        cost, known = self._cost(usage, pt, ct)
        rec = CallRecord(mode, purpose, True, _ms(t0), 200, pt, ct, cost, known)
        return ChatResponse(_content_text(resp), rec, resp)

    # ------------------------------------------------------------ строгий JSON

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        model_cls: type[T],
        *,
        schema_name: str = "response",
        max_tokens: int = 3000,
        modes: Sequence[Mode] = MODES,
        repair: bool = True,
    ) -> ChatJsonResult[T]:
        """Строгий JSON по pydantic-модели с деградацией режимов и одним repair-запросом."""
        schema = wire_schema(model_cls)
        calls: list[CallRecord] = []
        repair_left = 1 if repair else 0
        last_error = "no modes"
        todo = [m for m in modes if m not in self._rejected_modes] or [modes[-1]]
        for mode in todo:
            msgs = _with_json_instructions(messages, mode, schema)
            try:
                resp = self.chat(
                    msgs, mode=mode, schema=schema, schema_name=schema_name, max_tokens=max_tokens
                )
            except LLMHTTPError as exc:
                calls.append(exc.record)  # type: ignore[attr-defined]
                if exc.status in (401, 403):
                    raise _permanent(f"LLM auth failed: HTTP {exc.status}", calls, "llm_auth") from exc
                if exc.status == 429 or exc.status >= 500:
                    raise _retryable(exc, calls) from exc
                if exc.status in _DEGRADE_STATUSES and mode != "plain":
                    self._rejected_modes.add(mode)
                    last_error = f"{mode} rejected: HTTP {exc.status}"
                    continue
                raise _permanent(f"LLM request rejected: HTTP {exc.status}: {exc.body[:200]}", calls) from exc
            except LLMRetryableError as exc:
                calls.append(exc.record)  # type: ignore[attr-defined]
                exc.calls = calls  # type: ignore[attr-defined]
                raise
            calls.append(resp.record)
            text = resp.text
            if not text.strip() and mode != "plain":
                # 200 без контента: провайдер молча не поддержал режим
                self._rejected_modes.add(mode)
                last_error = f"{mode}: empty content"
                continue
            try:
                value = model_cls.model_validate(extract_json(text))
                return ChatJsonResult(value, mode, False, self.provider, self.model, calls, text)
            except (ValueError, ValidationError) as exc:
                last_error = _short_validation_error(exc)
                if resp.truncated:
                    last_error = f"{_TRUNCATED_HINT}; {last_error}"
            if repair_left <= 0:
                break
            repair_left -= 1
            fix_msgs = [
                *msgs,
                {"role": "assistant", "content": text[:6000]},
                {"role": "user", "content": _repair_prompt(last_error, schema)},
            ]
            try:
                resp2 = self.chat(
                    fix_msgs,
                    mode=mode,
                    schema=schema,
                    schema_name=schema_name,
                    max_tokens=max_tokens,
                    purpose="repair",
                )
            except LLMHTTPError as exc:
                calls.append(exc.record)  # type: ignore[attr-defined]
                if exc.status == 429 or exc.status >= 500:
                    raise _retryable(exc, calls) from exc
                raise _permanent(f"LLM repair rejected: HTTP {exc.status}", calls) from exc
            except LLMRetryableError as exc:
                calls.append(exc.record)  # type: ignore[attr-defined]
                exc.calls = calls  # type: ignore[attr-defined]
                raise
            calls.append(resp2.record)
            try:
                value = model_cls.model_validate(extract_json(resp2.text))
                return ChatJsonResult(value, mode, True, self.provider, self.model, calls, resp2.text)
            except (ValueError, ValidationError) as exc:
                last_error = _short_validation_error(exc)
                break
        raise LLMSchemaError(f"LLM JSON failed: {last_error}", calls=calls)


def _ms(t0: float) -> int:
    return int(round((time.monotonic() - t0) * 1000))


def _retry_after(headers: Any) -> float | None:
    raw = headers.get("retry-after") if headers is not None else None
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except (TypeError, ValueError):
        return None  # HTTP-дата: оставляем backoff очереди


def _retryable(exc: LLMHTTPError, calls: list[CallRecord]) -> LLMRetryableError:
    err = LLMRetryableError(str(exc), retry_after_sec=exc.retry_after_sec)
    err.calls = calls  # type: ignore[attr-defined]
    return err


def _permanent(msg: str, calls: list[CallRecord], error_class: str = "llm_rejected") -> LLMPermanentError:
    err = LLMPermanentError(msg, error_class=error_class)
    err.calls = calls  # type: ignore[attr-defined]
    return err


def _schema_hint(schema: dict[str, Any]) -> str:
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))


def _with_json_instructions(messages: list[dict[str, Any]], mode: Mode, schema: dict[str, Any]) -> list[dict]:
    """В json_object/plain схему сервер не знает — кладём её в последнее сообщение пользователя."""
    if mode == "json_schema":
        return list(messages)
    tail = (
        "\n\nОтветь ТОЛЬКО JSON-объектом (без пояснений и без markdown), строго по JSON Schema:\n"
        + _schema_hint(schema)
    )
    out = [dict(m) for m in messages]
    for m in reversed(out):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                # мультимодальное сообщение (текст + кадры): дописываем отдельной текстовой частью
                m["content"] = [*content, {"type": "text", "text": tail.strip()}]
            else:
                m["content"] = f"{content}{tail}"
            break
    else:
        out.append({"role": "user", "content": tail.strip()})
    return out


def message_text(content: Any) -> str:
    """Текст сообщения: строка как есть, список частей — только текстовые части через перевод строки."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content or "")


def _repair_prompt(error: str, schema: dict[str, Any]) -> str:
    return (
        "Твой предыдущий ответ не прошёл проверку JSON (JSON validation failed): "
        f"{error}\nИсправь и верни ТОЛЬКО корректный JSON-объект по схеме, без пояснений:\n"
        + _schema_hint(schema)
    )


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "MODES",
    "CallRecord",
    "ChatJsonResult",
    "ChatResponse",
    "HttpxTransport",
    "LLMClient",
    "LLMError",
    "LLMHTTPError",
    "LLMPermanentError",
    "LLMRetryableError",
    "LLMSchemaError",
    "MockTransport",
    "ScriptedTransport",
    "extract_json",
]
