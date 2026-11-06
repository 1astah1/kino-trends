"""YAML из загрузок оператора (импорт ниши, шаблона баннера): без ссылок `*alias`.

`yaml.safe_load` разрешает якоря и ссылки (`&a` / `*a`) и делит узлы между ними, но любой
последующий обход (str(), pydantic, сравнение с текущим конфигом) разворачивает их: «billion laughs»
на 449 байт даёт строку ~5 ГБ. Свои файлы проекта (config/*.yaml) читаются обычным safe_load.
"""

from __future__ import annotations

from typing import Any

import yaml
from yaml.composer import ComposerError

__all__ = ["load_untrusted_yaml"]


class _NoAliasLoader(yaml.SafeLoader):
    """SafeLoader, который отказывает на первой ссылке `*alias` (в том числе в `<<: *base`)."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise ComposerError(
                None, None, "ссылки YAML (*alias) не поддерживаются — раскройте их", event.start_mark
            )
        return super().compose_node(parent, index)


def load_untrusted_yaml(text: str) -> Any:
    """Как `yaml.safe_load`, но ссылка `*alias` → `yaml.YAMLError` (ComposerError)."""
    loader = _NoAliasLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()
