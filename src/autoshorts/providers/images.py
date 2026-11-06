"""
Генерация статичных картинок под Ken Burns — не AI-видео (ТЗ §1: разница в 20 раз по цене,
$0.05 против $1.08 за ролик). flux-schnell на fal.ai — $0.003/картинку по прайсу.

Mock-режим работает без ключа: генерирует testsrc2-заглушки через ffmpeg (та же текстура,
что уже используется в тестах — не плоский цвет, чтобы не ловить ложный freezedetect).
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx

from autoshorts.config import SETTINGS
from autoshorts.providers.registry import ImageProviderSpec, resolve_image

FAL_QUEUE_BASE = "https://queue.fal.run"


def _mock_generate_image(prompt: str, out_path: Path, seed: int, width: int = 1080, height: int = 1920) -> None:
    """Детерминированная по seed заглушка — testsrc2 с вариацией оттенка по seed, не плоский цвет."""
    hue_shift = (seed % 360)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"testsrc2=s={width}x{height}:d=1,format=yuv420p,hue=h={hue_shift}",
        "-frames:v", "1", str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _fal_generate_image(spec: ImageProviderSpec, prompt: str, out_path: Path, seed: int,
                         width: int = 1080, height: int = 1920) -> float:
    """
    fal.ai queue API: POST на очередь, поллинг статуса, скачивание результата.
    Возвращает latency в секундах. Бросает исключение при ошибке — вызывающий код (orchestrator)
    решает, откатываться ли на mock или проваливать job (см. ТЗ §9: платные шаги — максимум
    2 попытки и проверка бюджета ПЕРЕД вызовом, это делает db.check_and_reserve_budget выше по стеку).
    """
    if not SETTINGS.fal_api_key:
        raise RuntimeError("FAL_API_KEY не задан — используйте mock-режим или заполните .env")

    headers = {"Authorization": f"Key {SETTINGS.fal_api_key}"}
    submit_url = f"{FAL_QUEUE_BASE}/{spec.default_model}"

    t0 = time.monotonic()
    resp = httpx.post(
        submit_url, headers=headers,
        json={"prompt": prompt, "image_size": {"width": width, "height": height}, "seed": seed},
        timeout=30,
    )
    resp.raise_for_status()
    request_id = resp.json()["request_id"]

    status_url = f"{submit_url}/requests/{request_id}/status"
    result_url = f"{submit_url}/requests/{request_id}"

    for _ in range(60):  # до ~60 секунд поллинга с шагом 1с
        status_resp = httpx.get(status_url, headers=headers, timeout=10)
        status_resp.raise_for_status()
        status = status_resp.json().get("status")
        if status == "COMPLETED":
            break
        time.sleep(1)
    else:
        raise TimeoutError(f"fal.ai не завершил генерацию за 60с (request_id={request_id})")

    result_resp = httpx.get(result_url, headers=headers, timeout=10)
    result_resp.raise_for_status()
    image_url = result_resp.json()["images"][0]["url"]

    image_data = httpx.get(image_url, timeout=30).content
    out_path.write_bytes(image_data)

    return time.monotonic() - t0


def generate_scene_image(prompt: str, out_path: Path, seed: int,
                          provider_id: str | None = None) -> tuple[Path, float, float]:
    """
    Возвращает (путь_к_файлу, latency_sec, cost_usd).
    provider_id=None -> mock, если FAL_API_KEY пуст (безопасный дефолт для разработки/тестов).
    """
    provider_id = provider_id or ("fal:flux-schnell" if SETTINGS.fal_api_key else "mock")
    spec = resolve_image(provider_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if spec.adapter == "mock":
        t0 = time.monotonic()
        _mock_generate_image(prompt, out_path, seed)
        return out_path, time.monotonic() - t0, 0.0

    latency = _fal_generate_image(spec, prompt, out_path, seed)
    return out_path, latency, spec.price_per_image_usd
