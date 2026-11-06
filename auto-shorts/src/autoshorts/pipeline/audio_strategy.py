"""
Платформенная развилка по звуку — прямое следствие рисёрча трендовых звуков (сентябрь 2026).

YOUTUBE (основная площадка, решение владельца): с марта 2026 действует original sound bonus —
для каналов до 50 тыс. подписчиков СВОЯ озвучка/музыка ранжируется алгоритмом ВЫШЕ трендового
лицензированного звука, и оставляет 100% рекламной доли против дележа с правообладателем при
лицензированном треке. Это не просто экономия (мы и так строили на Silero/edge-tts) — это прямое
попадание в то, что вознаграждает именно наша основная площадка. Трендовый звук на YouTube —
осознанный редкий выбор, не дефолт.

Дополнительное ограничение: библиотека звуков Shorts защищает от копирайта только треки,
добавленные через ВСТРОЕННЫЙ пикер в приложении — видео, загруженное через API с вшитым чужим
треком, такой защиты не имеет и подлежит обычному Content ID (блокировка ролика целиком,
если >60 сек). Поскольку публикация у нас РУЧНАЯ (решение владельца), человек при заливке
технически МОЖЕТ выбрать трендовый звук через нативный пикер YouTube Studio/приложения —
это единственный Content-ID-безопасный способ добавить чужой тренд на YouTube.

VK (второй контур): своего трекера трендов у VK НЕТ и не будет — audio.get/search/getPopular
закрыты для сторонних приложений ещё с 2016 года, а 09.02.2026 VK добила последние
недокументированные лазейки. VK-тренды по наблюдениям практиков идут с лагом 1-3 недели за
TikTok/Reels — поэтому сигнал берётся оттуда (Pentos/tokchart), а не с самого VK.
Beat-sync (pipeline/beat_sync.py) применяется именно в VK-ветке.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AudioStrategy:
    use_own_voiceover: bool
    use_trending_sound: bool
    trending_sound_note: str
    beat_sync_enabled: bool


def resolve_audio_strategy(platform: str, channel_subscriber_count: int = 0) -> AudioStrategy:
    if platform == "youtube":
        # original sound bonus действует для каналов до 50k — у нас именно такие на старте
        if channel_subscriber_count < 50_000:
            return AudioStrategy(
                use_own_voiceover=True,
                use_trending_sound=False,
                trending_sound_note=(
                    "YouTube original sound bonus (с марта 2026): своя озвучка ранжируется выше "
                    "тренда для каналов <50k, плюс 100% рекламной доли вместо дележа. Если очень "
                    "нужен трендовый звук — добавлять ТОЛЬКО через нативный пикер при РУЧНОЙ заливке "
                    "(единственный Content-ID-безопасный путь), не вшивать в файл на этапе рендера."
                ),
                beat_sync_enabled=False,
            )
        return AudioStrategy(
            use_own_voiceover=True, use_trending_sound=True,
            trending_sound_note="Канал вырос за 50k — original sound bonus больше не действует, тренд можно тестировать смелее.",
            beat_sync_enabled=True,
        )

    if platform == "vk":
        return AudioStrategy(
            use_own_voiceover=False,
            use_trending_sound=True,
            trending_sound_note=(
                "VK своего трекера трендов не даёт (audio.* закрыты с 2016, лазейки добиты 09.02.2026). "
                "Сигнал берём с TikTok/Reels с лагом 1-3 недели (Pentos/tokchart). Только из библиотеки VK "
                "(во избежание детектора 'Немезида'), музыка вне официального каталога режет охват."
            ),
            beat_sync_enabled=True,
        )

    # tiktok и прочие — трендовый звук ожидаем, но баннер там всё равно запрещён API (ТЗ §7)
    return AudioStrategy(
        use_own_voiceover=False, use_trending_sound=True,
        trending_sound_note="Площадка вне основного плана — трендовый звук уместен, но публикация вручную.",
        beat_sync_enabled=True,
    )
