"""Коды атрибуции (M3-B, `domain/tags.py`): алфавит, опечатки, суффикс `_l`, алиасы."""

from __future__ import annotations

import re

import pytest

from autoshorts.domain import tags


def test_package_code_alphabet_has_no_confusable_glyphs() -> None:
    assert len(tags.PACKAGE_CODE_ALPHABET) == 30
    for bad in "0o1liu":
        assert bad not in tags.PACKAGE_CODE_ALPHABET
    assert tags.PACKAGE_CODE_ALPHABET == tags.PACKAGE_CODE_ALPHABET.lower()


def test_generated_codes_are_valid_and_varied() -> None:
    codes = {tags.generate_package_code() for _ in range(2000)}
    assert len(codes) > 1990
    for code in codes:
        assert len(code) == 5
        assert tags.is_package_code(code)
        assert tags.is_valid_code(code)
        assert not set(code) & set("0o1liu")
        assert re.fullmatch(r"[A-Za-z0-9_-]{2,64}", tags.with_landing_suffix(code))


def test_unique_code_skips_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    taken = {"aaaaa"}
    seq = iter(["a"] * 5 + list("bcdef"))
    monkeypatch.setattr(tags, "generate_package_code", lambda rng=None: "".join(next(seq) for _ in range(5)))
    assert tags.unique_code(lambda c: c in taken) == "bcdef"


def test_unique_code_gives_up() -> None:
    with pytest.raises(RuntimeError):
        tags.unique_code(lambda c: True, attempts=3)


@pytest.mark.parametrize(
    ("tag", "code", "via_landing"),
    [
        ("k7m2q_l", "k7m2q", True),
        ("k7m2q", "k7m2q", False),
        ("yt_dQw4w9WgXcQ_l", "yt_dQw4w9WgXcQ", True),
        ("vk_-123_456_l", "vk_-123_456", True),
        ("_l", "_l", False),
        ("ab_lx", "ab_lx", False),
    ],
)
def test_landing_suffix_is_stripped(tag: str, code: str, via_landing: bool) -> None:
    assert tags.split_landing_suffix(tag) == (code, via_landing)


def test_typo_fixes_for_package_codes() -> None:
    # регистр: автозаглавная буква телефона
    assert tags.lookup_candidates("K7M2Q") == ["K7M2Q", "k7m2q"]
    # u исключена из алфавита — набрали вместо v
    assert "k7v2q" in tags.lookup_candidates("k7u2q")
    # Crockford-свёртка o→0, i/l→1 (ручные коды из 5 символов могут содержать 0 и 1)
    assert tags.fold_package_code("PrOmo") == "pr0m0"
    assert tags.fold_package_code("B2IL9") == "b2119"
    assert "b2119" in tags.lookup_candidates("B2IL9")
    # точное совпадение идёт первым
    assert tags.lookup_candidates("k7m2q") == ["k7m2q"]


def test_aliases_are_case_sensitive_and_not_folded() -> None:
    assert tags.lookup_candidates("yt_dQw4w9WgXcQ") == ["yt_dQw4w9WgXcQ"]
    assert tags.lookup_candidates("yt_ab") == ["yt_ab"]  # длина 5, но алиас — без свёртки
    assert tags.lookup_candidates("vk_-1_2") == ["vk_-1_2"]


@pytest.mark.parametrize("bad", ["", "a", "<script>", "код", "k7m2q ", "a/b", "x" * 65, "%3Cscript%3E"])
def test_invalid_codes_have_no_candidates(bad: str) -> None:
    assert tags.lookup_candidates(bad) == []
    assert not tags.is_valid_code(bad)


def test_aliases() -> None:
    assert tags.youtube_alias("dQw4w9WgXcQ") == "yt_dQw4w9WgXcQ"
    assert tags.vk_alias("-123", "456") == "vk_-123_456"
    assert tags.vk_alias(77, 88) == "vk_77_88"
    with pytest.raises(tags.CodeError):
        tags.youtube_alias("short")
    with pytest.raises(tags.CodeError):
        tags.vk_alias("abc", "1")


@pytest.mark.parametrize(
    ("code", "problem"),
    [
        ("promo2026", None),
        ("SALE-1", None),
        ("health", "code_reserved"),
        ("Go", "code_reserved"),
        ("promo_l", "code_landing_suffix"),
        ("yt_something", "code_alias_prefix"),
        ("VK_1_2", "code_alias_prefix"),
        ("код", "code_alphabet"),
        ("x" * 63, "code_too_long"),
    ],
)
def test_manual_code_rules(code: str, problem: str | None) -> None:
    assert tags.manual_code_problem(code) == problem
