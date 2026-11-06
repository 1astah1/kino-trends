"""Роутеры `/api/v1`, по файлу на область (docs/ARCHITECTURE.md §10.1 п. 2).

`api/app.py` подключает каждый модуль этого пакета, у которого есть атрибут `router` (APIRouter),
с префиксом `/api/v1`. Добавление области не трогает общих файлов.

Соглашения для роутеров:
- пути латиницей через дефис, параметры пути `{job_id}`, `{product_id}`;
- у каждого роутера один тег (`APIRouter(tags=["jobs"])`), `name=` у эндпоинта — глагол:
  operationId строится как `<tag>_<name>` (`jobs_list`, `jobs_create`, …);
- статические пути объявляются раньше параметрических (`/jobs/stage-counts` до `/jobs/{job_id}`).

Заглушки M1-0 отвечают 501 `not_implemented`; владельцы областей заменяют тела, не меняя контракт
без согласования с потоком контрактов.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse


class EventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


class YamlResponse(PlainTextResponse):
    media_type = "text/yaml"


class PngResponse(Response):
    media_type = "image/png"


def not_implemented() -> JSONResponse:
    """501 в едином формате ошибок (§5.1)."""
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "code": "not_implemented",
                "message": "Эндпоинт ещё не реализован",
                "details": {},
            }
        },
    )
