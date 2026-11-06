"""
Воркер очереди: SELECT ... FOR UPDATE SKIP LOCKED (ТЗ §9).
Никакого Celery/Temporal/Airflow — единицы задач в час не требуют distributed task queue,
а SKIP LOCKED даёт параллельным воркерам работать без толкотни за одну и ту же задачу.

M0 (ARCHITECTURE §3.3 п.1): стадия voice идёт через edge-tts с boundary="WordBoundary"
(offline=False). Раньше здесь стоял offline=True, и в прод уходил синус 220 Гц вместо озвучки.
При APP_ENV=prod воркер не стартует, если хоть один провайдер оказался бы mock, пока не
выставлен ALLOW_MOCK_IN_PROD=true.
"""
from __future__ import annotations

import logging
import os
import socket
import time

from autoshorts import db
from autoshorts.config import SETTINGS
from autoshorts.models import MAX_STAGE_ATTEMPTS, JobStatus
from autoshorts.pipeline.orchestrator import run_stage_qc, run_stage_render, run_stage_script, run_stage_voice
from autoshorts.pipeline.voice import MockInProdError, is_prod, mock_allowed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("autoshorts.worker")

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"

# Порядок стадий и обработчик каждой. topic для script-стадии в проде идёт из trend_briefs
# или из ручного ввода — здесь оставлена точка расширения.
STAGE_HANDLERS = {
    JobStatus.QUEUED.value: ("script", lambda job: run_stage_script(job, topic=job["stage_payload"].get("topic", "отношения"))),
    # Реальная озвучка edge-tts. Транспортные сбои повторяются внутри synthesize_voiceover,
    # ноль WordBoundary-кьюсов — ошибка стадии (ретрай до MAX_STAGE_ATTEMPTS, потом rejected).
    JobStatus.SCRIPT_READY.value: ("voice", lambda job: run_stage_voice(job, offline=False)),
    JobStatus.VOICED.value: ("render", lambda job: run_stage_render(job)),
    JobStatus.RENDERED.value: ("qc", lambda job: run_stage_qc(job)),
}


def mock_providers_in_use() -> list[str]:
    """Какие провайдеры при текущем окружении отработали бы mock-заглушкой.

    Повторяет правила выбора из providers/llm.py (mock при LLM_PROVIDER=mock или пустом ключе)
    и providers/images.py (mock при пустом FAL_API_KEY). Озвучка после M0 mock не использует.
    """
    found: list[str] = []
    if SETTINGS.llm_provider == "mock" or not SETTINGS.llm_api_key:
        found.append("llm")
    if not SETTINGS.fal_api_key:
        found.append("images")
    return found


def assert_no_mock_in_prod() -> None:
    """В prod mock-провайдеры запрещены без ALLOW_MOCK_IN_PROD (ARCHITECTURE §3.3 п.1)."""
    if not is_prod() or mock_allowed():
        return
    mocks = mock_providers_in_use()
    if mocks:
        raise MockInProdError(
            f"APP_ENV=prod, но провайдеры {', '.join(mocks)} отработали бы mock-заглушкой. "
            f"Заполните LLM_PROVIDER/LLM_API_KEY и FAL_API_KEY или явно выставьте ALLOW_MOCK_IN_PROD=true."
        )


def process_one_cycle() -> bool:
    """
    Одна попытка забрать и продвинуть задачу. Возвращает True, если что-то обработано.

    ВАЖНО (найдено на реальном проде — стадия voice падала на edge-tts WSS-проблеме и крутилась
    бесконечно без бэкоффа, долбя внешний сервис каждую итерацию): попытки ограничены
    MAX_STAGE_ATTEMPTS. После лимита задача уходит в rejected с reject_reason='tehnicheskiy_sboy'
    вместо вечного цикла — совпадает с предупреждением из ТЗ §9 про Celery
    task_reject_on_worker_lost: "This can cause infinite message loops".
    """
    for from_status, (stage_name, handler) in STAGE_HANDLERS.items():
        job = db.claim_next_job(WORKER_ID, from_status)
        if job is None:
            continue
        logger.info(f"[{WORKER_ID}] job={job['video_uid']} stage={stage_name} started")
        try:
            handler(job)
            logger.info(f"[{WORKER_ID}] job={job['video_uid']} stage={stage_name} OK")
        except Exception as e:
            attempts = db.increment_attempt_count(job["id"])
            if attempts >= MAX_STAGE_ATTEMPTS:
                logger.error(
                    f"[{WORKER_ID}] job={job['video_uid']} stage={stage_name} "
                    f"провалена {attempts} раз подряд — уходит в rejected, не повторяем: {e}"
                )
                db.update_job_status(job["id"], JobStatus.REJECTED, reject_reason="tehnicheskiy_sboy")
            else:
                logger.exception(
                    f"[{WORKER_ID}] job={job['video_uid']} stage={stage_name} "
                    f"FAILED (попытка {attempts}/{MAX_STAGE_ATTEMPTS}): {e}"
                )
                db.update_job_status(job["id"], from_status)  # возвращаем в очередь на ту же стадию
        return True
    return False


def run_forever(poll_interval_sec: float = 5.0) -> None:
    assert_no_mock_in_prod()
    logger.info(f"Воркер {WORKER_ID} запущен")
    while True:
        did_work = process_one_cycle()
        if not did_work:
            time.sleep(poll_interval_sec)


if __name__ == "__main__":
    run_forever()
