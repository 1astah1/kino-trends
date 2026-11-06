"""Аналитика (docs/ARCHITECTURE.md §6.6, §6.7; поток M4-B).

* `interp` — просмотры на ровный возраст и на конец бизнес-дня: линейная интерполяция между снимками,
  без экстраполяции;
* `formulas` — итоги и доли (посетители, пользователи, честная цена опубликованного ролика, ROMI);
* `stats` — Манн–Уитни, доверительный интервал Пуассон–Гамма, A/B между роликами;
* `rollups` — ролл-апы `stats_daily_views`, `stats_daily_traffic`, `stats_daily_unattributed`.

Модули `interp`, `formulas` и `stats` не ходят в БД: их проверяют юнит-тесты
`tests/unit/test_{interp,formulas,stats}.py`. SQL — в `repos/analytics.py` и `rollups.py`.
"""
