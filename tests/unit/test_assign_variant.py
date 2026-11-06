"""Назначение варианта баннера ролику: детерминированно по video_uid, распределение — по весам (χ²)."""

from __future__ import annotations

import secrets
from collections import Counter

import pytest

from autoshorts.services.campaigns import choose_weighted, targeting_matches

N = 10_000


def _uids(n: int, seed: int = 1) -> list[str]:
    # video_uid в проекте — 12 hex; берём детерминированный набор, чтобы тест не мигал
    import random

    rnd = random.Random(seed)
    return [f"{rnd.getrandbits(48):012x}" for _ in range(n)]


def test_same_uid_same_variant() -> None:
    variants = [(11, 50), (12, 30), (13, 20)]
    for uid in _uids(200):
        first = choose_weighted(variants, f"7:{uid}")
        assert first is not None
        assert all(choose_weighted(variants, f"7:{uid}") == first for _ in range(3))
        # порядок перечисления вариантов не влияет
        assert choose_weighted(list(reversed(variants)), f"7:{uid}") == first


def test_distribution_matches_weights_chi_square() -> None:
    from scipy.stats import chisquare

    variants = [(1, 50), (2, 30), (3, 20)]
    counts = Counter(choose_weighted(variants, f"42:{uid}") for uid in _uids(N))
    total_w = sum(w for _, w in variants)
    observed = [counts[v] for v, _ in variants]
    expected = [N * w / total_w for _, w in variants]
    assert sum(observed) == N
    stat, p = chisquare(observed, expected)
    assert p > 0.001, (observed, expected, stat)


def test_distribution_random_uids_uniform_two_variants() -> None:
    from scipy.stats import chisquare

    variants = [(5, 1), (6, 1)]
    counts = Counter(choose_weighted(variants, f"9:{secrets.token_hex(6)}") for _ in range(N))
    _, p = chisquare([counts[5], counts[6]])
    assert p > 0.0001


def test_zero_weight_never_chosen_and_empty_pool() -> None:
    variants = [(1, 0), (2, 10), (3, 0)]
    assert {choose_weighted(variants, uid) for uid in _uids(500)} == {2}
    assert choose_weighted([(1, 0)], "x") is None
    assert choose_weighted([], "x") is None


def test_campaign_salt_changes_assignment() -> None:
    variants = [(1, 1), (2, 1)]
    uids = _uids(400)
    a = [choose_weighted(variants, f"1:{u}") for u in uids]
    b = [choose_weighted(variants, f"2:{u}") for u in uids]
    assert a != b  # разные кампании не коррелируют


@pytest.mark.parametrize(
    ("targeting", "ctx", "ok"),
    [
        ({}, {"channel_id": "c1", "niche_id": "taro", "platform": "youtube", "kind": "generate"}, True),
        (
            {"platforms": ["vk"]},
            {"channel_id": "c1", "niche_id": "taro", "platform": "youtube", "kind": "clip"},
            False,
        ),
        (
            {"niche_ids": ["taro"], "kinds": ["clip"]},
            {"channel_id": None, "niche_id": "taro", "platform": None, "kind": "clip"},
            True,
        ),
        (
            {"channel_ids": ["c2"]},
            {"channel_id": "c1", "niche_id": None, "platform": None, "kind": None},
            False,
        ),
    ],
)
def test_targeting(targeting: dict, ctx: dict, ok: bool) -> None:
    assert targeting_matches(targeting, **ctx) is ok
