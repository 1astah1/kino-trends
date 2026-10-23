"""OpenAI-совместимый клиент (OpenRouter и любой похожий endpoint) с картинками и разбором JSON-ответов."""
from __future__ import annotations

import base64
import json
import re
import sqlite3
import time
from dataclasses import dataclass

import httpx

from .db import now_iso

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class LLMError(RuntimeError):
    pass


def image_mime(data: bytes) -> str:
    """YouTube отдаёт WebP под именем .jpg; провайдеры сверяют тип с байтами."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"GIF":
        return "image/gif"
    return "image/jpeg"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None


def parse_json(text: str):
    """JSON из ответа модели: снимает ограждения ```, терпит текст до и после объекта (берёт первый полный объект или массив)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for i, ch in enumerate(t):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(t, i)
                return obj
            except json.JSONDecodeError:
                continue
    raise LLMError(f"не JSON: {t[:120]}")


class LLMError(RuntimeError):
    pass


def image_mime(data: bytes) -> str:
    """YouTube отдаёт WebP под именем .jpg; провайдеры сверяют тип с байтами."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"GIF":
        return "image/gif"
    return "image/jpeg"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None


def parse_json(text: str):
    """Достаёт JSON из ответа модели: с ограждением ``` или без, с мусором вокруг."""
    if not text:
        raise LLMError("пустой ответ модели")
    m = _FENCE.search(text)
    raw = m.group(1) if m else text
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"не JSON: {e}") from e
    raise LLMError("в ответе нет JSON")


class LLM:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def chat(self, messages: list[dict], max_tokens: int = 700, temperature: float = 0.2, json_mode: bool = True) -> tuple[str, Usage]:
        if not self.configured:
            raise LLMError("модель не настроена: нужны llm_base_url, llm_api_key, llm_model")
        body: dict = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if "openrouter.ai" in self.base_url:
            body["usage"] = {"include": True}
            if any(k in self.model for k in ("gpt-5", "o4", "o3")):
                body["reasoning"] = {"effort": "low"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "HTTP-Referer": "https://github.com/kino-trends", "X-Title": "kino-trends"}
        last_err = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.timeout) as c:
                    r = c.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            except httpx.HTTPError as e:
                last_err = f"сеть: {e}"
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 200:
                data = r.json()
                choices = data.get("choices") or []
                if not choices:
                    raise LLMError(f"нет choices: {str(data)[:200]}")
                text = (choices[0].get("message") or {}).get("content") or ""
                if isinstance(text, list):  # некоторые провайдеры отдают список блоков
                    text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
                u = data.get("usage") or {}
                usage = Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0),
                              float(u["cost"]) if u.get("cost") is not None else None)
                return text, usage
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 400 and json_mode and "response_format" in r.text:
                body.pop("response_format", None)
                json_mode = False
                continue
            raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
        raise LLMError(last_err or "модель не ответила")

    def ask_json(self, system: str, user: str, image: bytes | None = None, max_tokens: int = 700) -> tuple[dict, Usage]:
        content: list[dict] | str
        if image:
            b64 = base64.b64encode(image).decode()
            content = [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": f"data:{image_mime(image)};base64,{b64}"}}]
        else:
            content = user
        text, usage = self.chat([{"role": "system", "content": system}, {"role": "user", "content": content}], max_tokens=max_tokens)
        return parse_json(text), usage


def record_usage(conn: sqlite3.Connection, purpose: str, model: str, usage: Usage | None, ok: bool = True, note: str | None = None) -> None:
    u = usage or Usage()
    conn.execute("INSERT INTO llm_usage(ts, purpose, model, prompt_tokens, completion_tokens, cost_usd, ok, note) VALUES (?,?,?,?,?,?,?,?)",
                 (now_iso(), purpose, model, u.prompt_tokens, u.completion_tokens, u.cost_usd, int(ok), note))
    conn.commit()


def usage_today(conn: sqlite3.Connection) -> dict:
    day = now_iso()[:10]
    r = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(prompt_tokens),0) p, COALESCE(SUM(completion_tokens),0) c, COALESCE(SUM(cost_usd),0) cost, SUM(ok=0) errors FROM llm_usage WHERE ts >= ?", (day,)).fetchone()
    return {"calls": r["n"], "prompt_tokens": r["p"], "completion_tokens": r["c"], "cost_usd": round(r["cost"] or 0, 4), "errors": r["errors"] or 0}


def llm_from_settings(settings: dict) -> LLM:
    return LLM(settings.get("llm_base_url") or "", settings.get("llm_api_key") or "", settings.get("llm_model") or "")
