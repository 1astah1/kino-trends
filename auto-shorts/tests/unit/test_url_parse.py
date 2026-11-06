"""Разбор ссылок YouTube и VK (M3-A, `domain/url_parse.py`): 25+ форм, кириллица и мусор — отказ."""

from __future__ import annotations

import pytest

from autoshorts.domain.url_parse import UnsupportedUrl, normalize_vk_owner, parse_video_url

YT = "dQw4w9WgXcQ"
YT2 = "a-B_c1D2e3F"

YOUTUBE_CASES = [
    # (url, platform_kind)
    (f"https://www.youtube.com/shorts/{YT}", "short"),
    (f"https://youtube.com/shorts/{YT}?feature=share", "short"),
    (f"https://youtube.com/shorts/{YT}?si=AbCdEf123", "short"),
    (f"https://m.youtube.com/shorts/{YT}", "short"),
    (f"youtube.com/shorts/{YT}", "short"),
    (f"www.youtube.com/shorts/{YT}/", "short"),
    (f"http://www.youtube.com/shorts/{YT}", "short"),
    (f"HTTPS://WWW.YOUTUBE.COM/shorts/{YT}", "short"),
    (f"https://youtu.be/{YT}", "video"),
    (f"https://youtu.be/{YT}?si=xyz&t=5", "video"),
    (f"youtu.be/{YT}", "video"),
    (f"https://www.youtube.com/watch?v={YT}", "video"),
    (f"https://www.youtube.com/watch?v={YT}&t=42s", "video"),
    (f"https://www.youtube.com/watch?feature=share&v={YT}", "video"),
    (f"https://m.youtube.com/watch?v={YT}#comments", "video"),
    (f"https://music.youtube.com/watch?v={YT}&list=RD", "video"),
    (f"https://www.youtube.com/embed/{YT}", "video"),
    (f"https://www.youtube-nocookie.com/embed/{YT}?rel=0", "video"),
    (f"https://www.youtube.com/live/{YT}?si=1", "video"),
    (f"https://www.youtube.com/v/{YT}", "video"),
    (f"https://www.youtube.com/e/{YT}", "video"),
    (f"https://studio.youtube.com/video/{YT}/edit", "video"),
    (f"https://www.youtube.com/attribution_link?a=x&u=/watch%3Fv%3D{YT}%26feature%3Dshare", "video"),
    (f"//www.youtube.com/shorts/{YT}", "short"),
]

VK_CASES = [
    # (url, video_id, kind)
    ("https://vk.com/clip-123456_456239017", "-123456_456239017", "clip"),
    ("https://vk.com/clip123456_456239017", "123456_456239017", "clip"),
    ("https://vk.ru/clip-123456_456239017", "-123456_456239017", "clip"),
    ("https://m.vk.com/clip-123456_456239017?from=feed", "-123456_456239017", "clip"),
    ("vk.com/clip-1_2", "-1_2", "clip"),
    ("https://vkvideo.ru/clip-123456_456239017", "-123456_456239017", "clip"),
    ("https://vk.com/video-123456_456239017", "-123456_456239017", "video"),
    ("https://vk.com/video123_456?list=ln-abc", "123_456", "video"),
    ("https://vkvideo.ru/video-123456_456239017", "-123456_456239017", "video"),
    ("https://vk.com/clips-123456?z=clip-123456_456239017", "-123456_456239017", "clip"),
    ("https://vk.com/clips?z=clip-7_8", "-7_8", "clip"),
    ("https://vk.com/wall-123_555?z=clip-123_456", "-123_456", "clip"),
    ("https://vk.com/video?z=video-123_456%2Fpl_cat_trends", "-123_456", "video"),
    ("https://vk.com/videos-123?z=video-123_456%2Fclub123%2Fpl_-123_-2", "-123_456", "video"),
    ("https://vk.com/video/@umka?z=video-123_456", "-123_456", "video"),
    ("https://vk.com/video_ext.php?oid=-123&id=456&hash=abcdef", "-123_456", "video"),
    ("https://vkvideo.ru/video_ext.php?oid=77&id=88", "77_88", "video"),
]


@pytest.mark.parametrize(("url", "kind"), YOUTUBE_CASES)
def test_youtube_forms(url: str, kind: str) -> None:
    r = parse_video_url(url)
    assert r.platform == "youtube"
    assert r.platform_video_id == YT
    assert r.platform_kind == kind
    assert r.alias_code == f"yt_{YT}"
    assert r.canonical_url.startswith("https://www.youtube.com/")
    assert YT in r.canonical_url
    assert r.owner_ref is None


def test_youtube_canonical_short_vs_video() -> None:
    assert parse_video_url(f"youtu.be/{YT2}").canonical_url == f"https://www.youtube.com/watch?v={YT2}"
    assert parse_video_url(f"youtube.com/shorts/{YT2}").canonical_url == (
        f"https://www.youtube.com/shorts/{YT2}"
    )


@pytest.mark.parametrize(("url", "video_id", "kind"), VK_CASES)
def test_vk_forms(url: str, video_id: str, kind: str) -> None:
    r = parse_video_url(url)
    assert r.platform == "vk"
    assert r.platform_video_id == video_id
    assert r.platform_kind == kind
    assert r.alias_code == f"vk_{video_id}"
    assert r.owner_ref == video_id.split("_")[0]
    prefix = "clip" if kind == "clip" else "video"
    assert r.canonical_url == f"https://vk.com/{prefix}{video_id}"


def test_at_least_25_forms_covered() -> None:
    assert len(YOUTUBE_CASES) + len(VK_CASES) >= 25


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("https://www.youtube.com/shorts/привет12345", "non_ascii"),
        ("https://вк.com/clip-1_2", "non_ascii"),
        ("https://youtube.com/shorts/ dQw4w9WgXcQ", "whitespace"),
        ("javascript:alert(1)//youtube.com/shorts/dQw4w9WgXcQ", "bad_scheme"),
        ("ftp://youtube.com/shorts/dQw4w9WgXcQ", "bad_scheme"),
        ("https://youtube.com.evil.example/shorts/dQw4w9WgXcQ", "unsupported_host"),
        ("https://evil.example/?u=youtube.com/shorts/dQw4w9WgXcQ", "unsupported_host"),
        ("https://tiktok.com/@x/video/123", "unsupported_host"),
        ("https://www.youtube.com/@umka_chat", "not_a_video"),
        ("https://www.youtube.com/playlist?list=PL123", "not_a_video"),
        ("https://www.youtube.com/shorts/dQw4w9WgXc", "bad_video_id"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQQ", "bad_video_id"),
        ("https://youtu.be/<script>", "bad_video_id"),
        ("https://vk.com/umka_chat", "not_a_video"),
        ("https://vk.com/clip-0_1", "not_a_video"),
        ("https://vk.com/video_ext.php?oid=abc&id=1", "bad_video_id"),
        ("x" * 2001, "too_long"),
        ("мусор", "non_ascii"),
    ],
)
def test_rejected(url: str, reason: str) -> None:
    with pytest.raises(UnsupportedUrl) as ei:
        parse_video_url(url)
    assert ei.value.reason == reason


def test_alias_fits_telegram_start_param() -> None:
    import re

    for url, *_ in [*YOUTUBE_CASES, *VK_CASES]:
        alias = parse_video_url(url).alias_code
        assert re.fullmatch(r"[A-Za-z0-9_-]{2,62}", alias), alias  # + "_l" ≤ 64


@pytest.mark.parametrize(
    ("ref", "owner"),
    [
        ("-123", "-123"),
        ("club123", "-123"),
        ("public77", "-77"),
        ("https://vk.com/club123", "-123"),
        ("vk.com/public5/", "-5"),
        ("id45", "45"),
        ("45", "45"),
        ("https://vk.com/umka", None),
        (None, None),
        ("", None),
    ],
)
def test_normalize_vk_owner(ref: str | None, owner: str | None) -> None:
    assert normalize_vk_owner(ref) == owner
