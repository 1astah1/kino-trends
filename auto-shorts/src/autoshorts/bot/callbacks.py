"""callback_data кнопок приёмки: короткие латинские строки ≤ 64 байт, без JSON.

ap:<job_id>:<e>            одобрить (e — ожидаемый статус: p = pending_review, q = qc_failed)
rw:<job_id>:<e>            на доработку (перезапуск со стадии по умолчанию)
rj:<job_id>:<e>            показать причины отклонения
rr:<job_id>:<e>:<reason>   отклонить с причиной (код RejectReason)
oq:<job_id>                принять вопреки QC (только admin)
un:<job_id>                отменить решение (≤ 10 мин)
bk:<job_id>:<e>            вернуть основные кнопки
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from autoshorts.domain.enums import JobStatus, RejectReason

MAX_CALLBACK_BYTES = 64

ACTIONS = {
    "ap": "approve",
    "rw": "rework",
    "rj": "reject_menu",
    "rr": "reject",
    "oq": "override_qc",
    "un": "undo",
    "bk": "back",
}
CODES = {v: k for k, v in ACTIONS.items()}
EXPECTED = {"p": JobStatus.PENDING_REVIEW.value, "q": JobStatus.QC_FAILED.value}
EXPECTED_CODES = {v: k for k, v in EXPECTED.items()}
NEEDS_EXPECTED = frozenset({"approve", "rework", "reject_menu", "reject", "back"})

_RE = re.compile(r"(?P<a>[a-z]{2}):(?P<id>[1-9][0-9]{0,17})(?::(?P<e>[pq]))?(?::(?P<r>[a-z_]{2,32}))?")


@dataclass(frozen=True)
class Callback:
    action: str
    job_id: int
    expected_status: str | None = None
    reject_reason: str | None = None


def encode(
    action: str, job_id: int, expected_status: str | None = None, reject_reason: str | None = None
) -> str:
    parts = [CODES[action], str(int(job_id))]
    if expected_status is not None:
        parts.append(EXPECTED_CODES[expected_status])
    if reject_reason is not None:
        parts.append(RejectReason(reject_reason).value)
    data = ":".join(parts)
    if len(data.encode("ascii")) > MAX_CALLBACK_BYTES:
        raise ValueError(f"callback_data too long: {data!r}")
    return data


def decode(data: str | None) -> Callback | None:
    """Разобрать callback_data. Любая неожиданная форма — None (кнопку игнорируем)."""
    if not data or len(data.encode("utf-8", "ignore")) > MAX_CALLBACK_BYTES:
        return None
    m = _RE.fullmatch(data)
    if m is None:
        return None
    action = ACTIONS.get(m["a"])
    if action is None:
        return None
    expected = EXPECTED.get(m["e"]) if m["e"] else None
    reason = m["r"]
    if action in NEEDS_EXPECTED and expected is None:
        return None
    if action == "reject":
        if reason is None or reason not in RejectReason._value2member_map_:
            return None
    elif reason is not None:
        return None
    return Callback(action=action, job_id=int(m["id"]), expected_status=expected, reject_reason=reason)
