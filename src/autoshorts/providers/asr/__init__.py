"""ASR providers for the clipping track (docs/ARCHITECTURE.md §6.3.2, §5.5 `AsrProvider`).

Every provider implements `transcribe(audio_path, language="ru", progress=None) -> Transcript`
(`autoshorts.clipping.transcript.Transcript`: words, sentences, vad, rtfx, cost_usd).

| key                | module              | notes                                                         |
|--------------------|---------------------|---------------------------------------------------------------|
| `gigaam_v3_onnx`   | `gigaam_onnx`       | default for ru; local, onnx-asr + Silero VAD, needs MODELS_DIR |
| `yandex_speechkit` | `yandex_speechkit`  | cloud, API v3 recognizeFileAsync (off by default)             |
| `openai_compat_asr`| `openai_compat`     | cloud, `/audio/transcriptions` with word timestamps           |
| `mock`             | `mock`              | tests/CI; refused in prod unless ALLOW_MOCK_IN_PROD           |

Importing this package is cheap: heavy modules (onnxruntime, onnx-asr, numpy) load only when a
provider is constructed through `get_asr_provider`.
"""

from __future__ import annotations

import importlib
from typing import Any

from autoshorts.providers.asr.base import AsrError, AsrProvider, AsrUnavailable

PROVIDERS: dict[str, tuple[str, str]] = {
    "gigaam_v3_onnx": ("autoshorts.providers.asr.gigaam_onnx", "GigaamOnnxProvider"),
    "yandex_speechkit": ("autoshorts.providers.asr.yandex_speechkit", "YandexSpeechKitProvider"),
    "openai_compat_asr": ("autoshorts.providers.asr.openai_compat", "OpenAICompatAsrProvider"),
    "mock": ("autoshorts.providers.asr.mock", "MockAsrProvider"),
}
ALIASES: dict[str, str] = {
    "gigaam": "gigaam_v3_onnx",
    "gigaam_onnx": "gigaam_v3_onnx",
    "speechkit": "yandex_speechkit",
    "openai_compat": "openai_compat_asr",
    "openai": "openai_compat_asr",
}
DEFAULT_PROVIDER = "gigaam_v3_onnx"


def provider_class(key: str) -> type:
    name = ALIASES.get(key, key)
    try:
        module_name, cls_name = PROVIDERS[name]
    except KeyError:
        raise ValueError(f"unknown ASR provider {key!r}; known: {sorted(PROVIDERS)}") from None
    return getattr(importlib.import_module(module_name), cls_name)


def get_asr_provider(key: str = DEFAULT_PROVIDER, **kwargs: Any) -> AsrProvider:
    """Construct a provider by key (aliases allowed). kwargs go to the provider constructor."""
    return provider_class(key)(**kwargs)


__all__ = [
    "ALIASES",
    "DEFAULT_PROVIDER",
    "PROVIDERS",
    "AsrError",
    "AsrProvider",
    "AsrUnavailable",
    "get_asr_provider",
    "provider_class",
]
