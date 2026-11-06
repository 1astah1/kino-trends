"""
Загрузка окружения и конфигов ниши/баннера.
Паттерн реестра провайдеров — см. providers/registry.py (идея из MoneyPrinterTurbo, ТЗ §2).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


@dataclass(frozen=True)
class Settings:
    database_url: str
    fal_api_key: str
    llm_provider: str
    llm_api_key: str
    llm_base_url: str
    telegram_bot_token: str
    telegram_review_chat_id: str
    redirect_domain: str
    telegram_bot_username: str
    render_tmp_dir: Path
    master_archive_dir: Path
    fonts_dir: Path
    daily_budget_usd: float

    @staticmethod
    def load() -> "Settings":
        def _p(key: str, default: str = "") -> Path:
            return Path(os.getenv(key, default))

        return Settings(
            database_url=os.getenv("DATABASE_URL", "postgresql://autoshorts:autoshorts@localhost:5432/autoshorts"),
            fal_api_key=os.getenv("FAL_API_KEY", ""),
            llm_provider=os.getenv("LLM_PROVIDER", "mock"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_base_url=os.getenv("LLM_BASE_URL", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_review_chat_id=os.getenv("TELEGRAM_REVIEW_CHAT_ID", ""),
            redirect_domain=os.getenv("REDIRECT_DOMAIN", "u.umka.chat"),
            telegram_bot_username=os.getenv("TELEGRAM_BOT_USERNAME", "umka_bot"),
            render_tmp_dir=_p("RENDER_TMP_DIR", "./render_tmp"),
            master_archive_dir=_p("MASTER_ARCHIVE_DIR", "./assets/master_archive"),
            fonts_dir=_p("FONTS_DIR", "./config/fonts"),
            daily_budget_usd=float(os.getenv("DAILY_BUDGET_USD", "15.0")),
        )


SETTINGS = Settings.load()
SETTINGS.render_tmp_dir.mkdir(parents=True, exist_ok=True)
SETTINGS.master_archive_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class NicheConfig:
    """Полная конфигурация ниши — один YAML прошивает все стадии. См. config/niches/*.yaml."""
    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def label(self) -> str:
        """Человекочитаемое название ниши для карточек в Telegram-боте (config/niches/*.yaml)."""
        return self.raw.get("label", self.raw["id"])

    @property
    def script(self) -> dict[str, Any]:
        return self.raw["script"]

    @property
    def forbidden_phrases(self) -> list[str]:
        return self.raw["script"].get("forbidden_phrases", [])

    @property
    def visuals(self) -> dict[str, Any]:
        return self.raw["visuals"]

    @property
    def voice(self) -> dict[str, Any]:
        return self.raw["voice"]

    @property
    def captions(self) -> dict[str, Any]:
        return self.raw["captions"]

    @property
    def duration_range(self) -> tuple[int, int]:
        d = self.raw["duration"]["target_sec"]
        return d[0], d[1]

    @property
    def loop_seam(self) -> bool:
        return self.raw["duration"].get("loop_seam", True)


def load_niche(niche_id: str) -> NicheConfig:
    path = CONFIG_DIR / "niches" / f"{niche_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Конфиг ниши не найден: {path}")
    with path.open(encoding="utf-8") as f:
        return NicheConfig(raw=yaml.safe_load(f))


def load_banner_config(platform: str = "youtube") -> dict[str, Any]:
    """Баннерный конфиг с платформенными оверрайдами (ТЗ §7)."""
    path = CONFIG_DIR / "banner.yaml"
    with path.open(encoding="utf-8") as f:
        base = yaml.safe_load(f)

    overrides = base.get("platform_overrides", {}).get(platform, {})
    merged = dict(base)
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **val}
        else:
            merged[key] = val
    return merged
