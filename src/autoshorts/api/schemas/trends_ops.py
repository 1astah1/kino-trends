"""Очередь: фон Kino-trends глазами консоли (экран `/queue`, тег `trends`, роутер `routers/trends_ops.py`).

Экран «Очередь» показывает всё, что система делает сама: задачи воркеров auto-shorts (`/tasks`,
`/workers`, `/system/status`) и фон Kino-trends — прогоны сбора трендов, фоновые работы (чарты, нейро,
словарь) и конвейер. Этот модуль типизирует вторую половину:

* `GET /trends/runs` → `TrendsRuns` (`/api/runs` Kino-trends): прогоны сбора, текущий прогон с фазой,
  прогрессом и хвостом лога, фоновые работы; плюс интервал автосбора и расчётное время следующего прогона.
* `GET /trends/runs/{run_id}/log` → `TrendsRunLog`.
* `POST /trends/runs` → `TrendsRunStarted`; `POST /trends/runs/cancel` → `TrendsRunCancelling`;
  `POST /trends/videos-refresh` → `TrendsVideosRefresh`; `POST /trends/pipeline/tick` → `TrendsPipelineTick`.
* `GET /trends/settings` → `TrendsSettings` — только тумблеры автоматики из белого списка `SETTING_SPECS`
  с русскими подписями и типами; `PUT /trends/settings` принимает `{key: value}` того же списка.

Kino-trends хранит настройки строками («0»/«1», числа текстом); здесь они приводятся к bool/int/str
и обратно, чтобы фронт работал с типами, а не со строками.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, RootModel

from autoshorts.api.schemas.common import ApiModel

__all__ = [
    "SETTING_KEYS",
    "SETTING_SPECS",
    "SettingKind",
    "SettingSpec",
    "TrendsBackgroundJob",
    "TrendsCurrentRun",
    "TrendsPipelineTick",
    "TrendsRun",
    "TrendsRunCancelling",
    "TrendsRunLog",
    "TrendsRunLogLine",
    "TrendsRunLogQueryParams",
    "TrendsRunStarted",
    "TrendsRuns",
    "TrendsRunsQueryParams",
    "TrendsSettingItem",
    "TrendsSettings",
    "TrendsSettingsPatch",
    "TrendsVideosRefresh",
    "TrendsVideosRefreshRequest",
]


class PassthroughModel(ApiModel):
    """Запись Kino-trends: известные поля типизированы, остальные проходят насквозь."""

    model_config = ConfigDict(from_attributes=True, extra="allow")


# ---------------------------------------------------------------- прогоны сбора (`/api/runs`)


class TrendsRunsQuery(BaseModel):
    limit: int = Field(30, ge=1, le=200, description="Сколько последних прогонов вернуть")


TrendsRunsQueryParams = Annotated[TrendsRunsQuery, Query()]


class TrendsRunLogQuery(BaseModel):
    limit: int = Field(300, ge=1, le=2000, description="Сколько последних строк лога вернуть")


TrendsRunLogQueryParams = Annotated[TrendsRunLogQuery, Query()]


class TrendsRun(PassthroughModel):
    """Прогон сбора трендов (таблица `runs` Kino-trends)."""

    id: int
    trigger: str | None = Field(None, description="manual | schedule")
    started_at: str | None = None
    finished_at: str | None = None
    status: str = Field(description="running | ok | error (значение Kino-trends, в схеме — строка)")
    stats: dict[str, Any] = Field(
        default_factory=dict, description="Счётчики прогона: new_videos, duration_s, …"
    )
    error: str | None = None


class TrendsRunLogLine(PassthroughModel):
    ts: str | None = None
    level: str | None = Field(None, description="info | warn | error")
    msg: str = ""


class TrendsCurrentRun(PassthroughModel):
    """Текущий сбор (`RunState.snapshot()`): фаза, прогресс «сделано/всего», хвост лога ≤ 80 строк."""

    running: bool = False
    run_id: int | None = None
    trigger: str | None = None
    started_at: str | None = None
    phase: str | None = Field(None, description="Название фазы по-русски: «хэштеги», «детали (Data API)», …")
    progress: str | int | float | None = Field(None, description="Прогресс фазы, обычно «сделано/всего что»")
    stats: dict[str, Any] = Field(default_factory=dict)
    log: list[TrendsRunLogLine] = Field(default_factory=list)


class TrendsBackgroundJob(PassthroughModel):
    """Фоновая работа Kino-trends вне прогона: dict_import, recognize, neuro, popular, sources_top."""

    running: bool = False
    progress: str | None = Field(None, description="Прогресс по-русски, если работа его сообщает")
    last: dict[str, Any] | None = Field(None, description="Итог последнего запуска: ok, at/ts, error, …")


class TrendsRuns(PassthroughModel):
    items: list[TrendsRun]
    current: TrendsCurrentRun
    jobs: dict[str, TrendsBackgroundJob] = Field(default_factory=dict)
    schedule_hours: int | None = Field(
        None,
        description="Интервал автосбора (настройка schedule_hours), 0 — только вручную; None — не узнали",
    )
    next_run_at: str | None = Field(
        None,
        description="Расчётное время следующего автосбора: старт последнего прогона + интервал (ISO, UTC)",
    )


class TrendsRunLog(ApiModel):
    items: list[TrendsRunLogLine]


class TrendsRunStarted(PassthroughModel):
    run_id: int
    state: TrendsCurrentRun


class TrendsRunCancelling(PassthroughModel):
    cancelling: bool = Field(description="Был ли прогон, которому отправлен сигнал остановки")


# ---------------------------------------------------------------- действия над фоном


class TrendsVideosRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charts_only: bool = Field(False, description="Только чарты, без нейросети и разбора кадров")


class TrendsVideosRefresh(PassthroughModel):
    ok: bool = True
    running: bool = Field(True, description="Работа «популярные видео» идёт (или уже шла)")


class TrendsPipelineTick(PassthroughModel):
    """Итог одного шага конвейера (`pipeline.tick`)."""

    processed: int = 0
    changed: int = 0
    sent: int = 0
    errors: int = Field(0, description="Заданий в состоянии failed после шага")
    skipped: str | None = Field(None, description="Почему шаг пропущен: auto-shorts не настроен / не готов")
    health: str | None = None


# ---------------------------------------------------------------- тумблеры автоматики (`/api/settings`)

SettingKind = Literal["bool", "int", "choice"]


class SettingSpec(BaseModel):
    """Описание одного ключа белого списка (`DEFAULT_SETTINGS` Kino-trends → русская подпись и тип)."""

    model_config = ConfigDict(frozen=True)

    key: str
    kind: SettingKind
    label: str
    description: str
    group: Literal["collect", "pipeline", "publish", "rights"]
    choices: tuple[str, ...] = ()
    min: int | None = None
    max: int | None = None
    unit: str | None = None


SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="schedule_hours",
        kind="int",
        label="Автосбор трендов",
        description="Как часто Kino-trends сам запускает сбор трендов. 0 — только по кнопке.",
        group="collect",
        min=0,
        max=168,
        unit="ч",
    ),
    SettingSpec(
        key="pop_auto_pick",
        kind="bool",
        label="Автоподборка роликов",
        description="Чистые ролики с баллом выше порога сами попадают в подборку под перезалив.",
        group="collect",
    ),
    SettingSpec(
        key="pipeline_enabled",
        kind="bool",
        label="Конвейер включён",
        description=(
            "Фоновый цикл Kino-trends двигает очередь: шлёт исходники в auto-shorts и следит за клипами."
        ),
        group="pipeline",
    ),
    SettingSpec(
        key="pipeline_tick_min",
        kind="int",
        label="Шаг конвейера",
        description="Как часто цикл конвейера делает шаг.",
        group="pipeline",
        min=1,
        max=1440,
        unit="мин",
    ),
    SettingSpec(
        key="pipeline_max_per_tick",
        kind="int",
        label="Заданий за шаг",
        description="Сколько заданий очереди обрабатывать за один шаг конвейера.",
        group="pipeline",
        min=1,
        max=500,
    ),
    SettingSpec(
        key="pipeline_clips_per_source",
        kind="int",
        label="Клипов из исходника",
        description="Сколько клипов резать из одного длинного исходника.",
        group="pipeline",
        min=1,
        max=20,
    ),
    SettingSpec(
        key="pipeline_auto_enqueue",
        kind="bool",
        label="Самому ставить в очередь",
        description="После каждого прогона ставить в конвейер слоты календаря на сегодня и подборку роликов.",
        group="pipeline",
    ),
    SettingSpec(
        key="pipeline_auto_review",
        kind="bool",
        label="Одобрять без приёмки",
        description=(
            "Клипы одобряются без ручной приёмки — QC и оригинальность всё равно проверяет auto-shorts."
        ),
        group="publish",
    ),
    SettingSpec(
        key="pipeline_autopublish",
        kind="bool",
        label="Автозалив одобренного",
        description=(
            "Одобренные пакеты сразу ставятся на автозалив: ролики уходят на канал без вашего участия."
        ),
        group="publish",
    ),
    SettingSpec(
        key="pipeline_publish_now",
        kind="bool",
        label="Заливать сразу",
        description="Включено — заливать немедленно; выключено — в ближайший слот плана канала.",
        group="publish",
    ),
    SettingSpec(
        key="rights_policy",
        kind="choice",
        label="Политика по правам",
        description="strict — только «проходит»; balanced — и «с оговорками»; off — права лишь помечаются.",
        group="rights",
        choices=("strict", "balanced", "off"),
    ),
)
"""Белый список настроек Kino-trends, которыми управляет экран «Очередь». Имена и типы — по
`kinotrends/config.py: DEFAULT_SETTINGS` (`0`/`1` для булевых, числа текстом, rights_policy — выбор)."""

SETTING_KEYS: frozenset[str] = frozenset(spec.key for spec in SETTING_SPECS)


class TrendsSettingItem(ApiModel):
    key: str
    kind: SettingKind
    label: str
    description: str
    group: str = Field(description="collect | pipeline | publish | rights")
    value: bool | int | str | None = Field(description="Текущее значение (None — Kino-trends не вернул ключ)")
    default: bool | int | str | None = Field(None, description="Значение по умолчанию в Kino-trends")
    choices: list[str] | None = Field(None, description="Допустимые значения для kind=choice")
    min: int | None = None
    max: int | None = None
    unit: str | None = None


class TrendsSettings(ApiModel):
    items: list[TrendsSettingItem]


class TrendsSettingsPatch(RootModel[dict[str, bool | int | str]]):
    """`{key: value}` только из белого списка `SETTING_SPECS`; неизвестный ключ или чужой тип — 422."""
