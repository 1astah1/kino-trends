"""Проверка загрузки и мержа конфигов ниши/баннера."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoshorts.config import load_banner_config, load_niche


def test_niche_loads_with_forbidden_phrases():
    n = load_niche("taro")
    assert n.id == "taro"
    assert len(n.forbidden_phrases) > 0
    assert "гарантированно" in n.forbidden_phrases


def test_niche_label_accessible():
    """Регрессия: NicheConfig.label падал AttributeError — карточка в боте никогда не отправлялась."""
    n = load_niche("taro")
    assert n.label == "Таро — выбери карту"


def test_niche_duration_in_safe_range():
    n = load_niche("taro")
    lo, hi = n.duration_range
    # см. открытый вопрос ТЗ §3: стартуем с 15-25с, вне спорной мёртвой зоны 26-44с
    assert lo == 15 and hi == 25


def test_banner_platform_override_vk_changes_safe_zone():
    base = load_banner_config("youtube")
    vk = load_banner_config("vk")
    assert vk["safe_zone"]["bottom_dead_above"] != base["safe_zone"]["bottom_dead_above"]


def test_banner_tiktok_disables_offer_layer():
    """TikTok API запрещает логотипы/вотермарки/промо-текст — см. ТЗ §7."""
    tiktok = load_banner_config("tiktok")
    assert tiktok["layer_2_offer_banner"]["enabled"] is False
