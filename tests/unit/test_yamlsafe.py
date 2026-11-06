"""YAML из загрузок оператора: ссылки `*alias` отвергаются до обхода дерева (alias bomb)."""

from __future__ import annotations

import time

import pytest
import yaml

from autoshorts.core.yamlsafe import load_untrusted_yaml
from autoshorts.domain.niche_schema import load_yaml


def _bomb(depth: int = 9, width: int = 10) -> str:
    lines = ['a0: &a0 "lol"']
    for i in range(1, depth + 1):
        refs = ", ".join(f"*a{i - 1}" for _ in range(width))
        lines.append(f"a{i}: &a{i} [{refs}]")
    lines.append(f"id: taro\nlabel: *a{depth}")
    return "\n".join(lines) + "\n"


def test_plain_yaml_and_anchor_without_alias_load() -> None:
    assert load_untrusted_yaml("id: taro\nlist: [1, 2]\nnested: {a: b}\n") == {
        "id": "taro",
        "list": [1, 2],
        "nested": {"a": "b"},
    }
    assert load_untrusted_yaml("base: &b {x: 1}\n") == {"base": {"x": 1}}
    assert load_untrusted_yaml("") is None


@pytest.mark.parametrize(
    "text",
    ["a: &x 1\nb: *x\n", "base: &b {x: 1}\nchild:\n  <<: *b\n  y: 2\n", _bomb()],
)
def test_alias_is_rejected(text: str) -> None:
    started = time.monotonic()
    with pytest.raises(yaml.YAMLError, match="alias"):
        load_untrusted_yaml(text)
    assert time.monotonic() - started < 1.0


def test_niche_load_yaml_rejects_bomb_as_value_error() -> None:
    with pytest.raises(ValueError, match="YAML не разбирается"):
        load_yaml(_bomb())
