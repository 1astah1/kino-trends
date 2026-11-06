"""
Реестр провайдеров как frozen dataclass — паттерн из MoneyPrinterTurbo (ТЗ §2, app/models/llm_provider.py).
Метаданные провайдера отдельно от вызова: "что это" здесь, "как звать" — в конкретном адаптере.
Новый провайдер (напр. другая модель на fal) добавляется одной записью, без правки логики вызова.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LLMProviderSpec:
    provider_id: str
    adapter: str                      # "openai_compatible" | "mock"
    default_model: str
    default_base_url: str = ""
    requires_api_key: bool = True
    extra_fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TTSProviderSpec:
    provider_id: str
    adapter: str                      # "silero_local" | "fal_tts" | "mock"
    default_model: str
    price_per_1000_chars_usd: float = 0.0
    requires_api_key: bool = False
    license_note: str = ""


@dataclass(frozen=True)
class ImageProviderSpec:
    provider_id: str
    adapter: str                      # "fal_image" | "mock"
    default_model: str
    price_per_image_usd: float = 0.0
    requires_api_key: bool = True


# --- LLM: сценарий ролика ---
LLM_PROVIDERS: dict[str, LLMProviderSpec] = {
    "mock": LLMProviderSpec(
        provider_id="mock", adapter="mock", default_model="mock-script-v1", requires_api_key=False,
    ),
    "gigachat": LLMProviderSpec(
        provider_id="gigachat", adapter="openai_compatible",
        default_model="GigaChat-Lite",
        default_base_url="https://gigachat.devices.sberbank.ru/api/v1",
    ),
    "openai_compatible": LLMProviderSpec(
        provider_id="openai_compatible", adapter="openai_compatible",
        default_model="gpt-4o-mini",
    ),
}

# --- TTS: озвучка. Silero v5_cis_base — единственный MIT-чекпоинт, см. лицензионный разбор в ТЗ §2 ---
TTS_PROVIDERS: dict[str, TTSProviderSpec] = {
    "mock": TTSProviderSpec(provider_id="mock", adapter="mock", default_model="mock-tts"),
    "silero:v5_cis_base": TTSProviderSpec(
        provider_id="silero:v5_cis_base", adapter="silero_local",
        default_model="v5_cis_base",
        price_per_1000_chars_usd=0.0,
        requires_api_key=False,
        license_note="MIT — единственный коммерчески безопасный чекпоинт Silero, НЕ v5_ru/v3/v4 (CC-BY-NC)",
    ),
    "fal:minimax-speech-2.8-hd": TTSProviderSpec(
        provider_id="fal:minimax-speech-2.8-hd", adapter="fal_tts",
        default_model="fal-ai/minimax/speech-2.8-hd",
        price_per_1000_chars_usd=0.10,
        requires_api_key=True,
    ),
}

# --- Изображения для визуала (Ken Burns через crop, НЕ AI-видео — см. экономику в ТЗ §1) ---
IMAGE_PROVIDERS: dict[str, ImageProviderSpec] = {
    "mock": ImageProviderSpec(provider_id="mock", adapter="mock", default_model="mock-image", requires_api_key=False),
    "fal:flux-schnell": ImageProviderSpec(
        provider_id="fal:flux-schnell", adapter="fal_image",
        default_model="fal-ai/flux/schnell",
        price_per_image_usd=0.003,
    ),
}


def resolve_llm(provider_id: str) -> LLMProviderSpec:
    if provider_id not in LLM_PROVIDERS:
        raise ValueError(f"Неизвестный LLM-провайдер: {provider_id}. Доступны: {list(LLM_PROVIDERS)}")
    return LLM_PROVIDERS[provider_id]


def resolve_tts(provider_id: str) -> TTSProviderSpec:
    if provider_id not in TTS_PROVIDERS:
        raise ValueError(f"Неизвестный TTS-провайдер: {provider_id}. Доступны: {list(TTS_PROVIDERS)}")
    return TTS_PROVIDERS[provider_id]


def resolve_image(provider_id: str) -> ImageProviderSpec:
    if provider_id not in IMAGE_PROVIDERS:
        raise ValueError(f"Неизвестный image-провайдер: {provider_id}. Доступны: {list(IMAGE_PROVIDERS)}")
    return IMAGE_PROVIDERS[provider_id]
