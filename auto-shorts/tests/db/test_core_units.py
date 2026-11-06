"""Модули `autoshorts.core`: config, security, ids, timeutil, logging, errors (без БД).

Лежат в tests/db/, потому что tests/unit/ не входит во владение потока M1-A (§10.2).
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoshorts.core import errors, ids, security, timeutil
from autoshorts.core import logging as aslog
from autoshorts.core.config import PROJECT_ROOT, EnvSettings, get_env, redact_conninfo, reload_env

# ---------------------------------------------------------------- config


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> EnvSettings:
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return EnvSettings(_env_file=None)


def test_config_defaults(monkeypatch) -> None:
    for name in (
        "APP_ENV",
        "MEDIA_ROOT",
        "SESSION_COOKIE_SECURE",
        "LOG_FORMAT",
        "RESOURCE_PROFILE",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)
    env = EnvSettings(_env_file=None)
    assert env.app_env == "dev" and not env.is_prod
    assert env.media_root == PROJECT_ROOT / "var" / "media"
    assert env.cookie_secure is False and env.effective_log_format == "text"
    assert env.resource_profile.value == "shared_dev"


def test_config_prod_aliases_and_derived(monkeypatch) -> None:
    env = _env(monkeypatch, APP_ENV="Production", LOG_LEVEL="warn")
    assert env.app_env == "prod" and env.is_prod
    assert env.cookie_secure is True and env.effective_log_format == "json"
    assert env.log_level == "WARNING"
    assert _env(monkeypatch, APP_ENV="prod", SESSION_COOKIE_SECURE="false").cookie_secure is False


def test_config_relative_paths_from_project_root(monkeypatch, tmp_path: Path) -> None:
    env = _env(monkeypatch, MEDIA_ROOT="data/media", BACKUP_DIR=str(tmp_path / "b"))
    assert env.media_root == PROJECT_ROOT / "data" / "media"
    assert env.backup_dir == tmp_path / "b"


def test_config_empty_values_ignored_and_bad_values_rejected(monkeypatch) -> None:
    assert _env(monkeypatch, AS_SECRETS_KEY="").as_secrets_key is None
    with pytest.raises(ValidationError):
        _env(monkeypatch, RESOURCE_PROFILE="huge")
    with pytest.raises(ValidationError):
        _env(monkeypatch, APP_ENV="прод")
    monkeypatch.setenv("APP_ENV", "dev")
    with pytest.raises(ValidationError):
        _env(monkeypatch, LOG_LEVEL="loud")


def test_get_env_cached_and_reloadable(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    first = reload_env()
    assert get_env() is first and first.log_level == "DEBUG"
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    assert get_env().log_level == "DEBUG"
    assert reload_env().log_level == "ERROR"
    get_env.cache_clear()


def test_config_import_creates_no_directories(tmp_path: Path) -> None:
    media = tmp_path / "not_created"
    code = "from autoshorts.core.config import get_env; get_env()"
    env = {"MEDIA_ROOT": str(media), "BACKUP_DIR": str(tmp_path / "b"), "PATH": "/usr/bin:/bin"}
    subprocess.run([sys.executable, "-c", code], check=True, env=env, cwd=tmp_path)
    assert not media.exists() and not (tmp_path / "b").exists() and list(tmp_path.iterdir()) == []


def test_redact_conninfo() -> None:
    red = redact_conninfo("postgresql://u:topsecret@db:5432/app")
    assert "topsecret" not in red and "dbname=app" in red


# ---------------------------------------------------------------- security


def test_password_hash_roundtrip(fast_password_hasher) -> None:
    h = security.hash_password("правильный пароль")
    assert h.startswith("$argon2id$")
    assert security.verify_password(h, "правильный пароль")
    assert not security.verify_password(h, "неправильный")
    assert not security.verify_password("not-a-hash", "x")
    security.dummy_verify("x")


def test_password_needs_rehash_after_param_change(fast_password_hasher) -> None:
    cheap = security.hash_password("pw-1234567890")
    previous = security.configure_password_hasher(time_cost=2, memory_cost=16, parallelism=1)
    try:
        assert security.password_needs_rehash(cheap)
        assert security.password_needs_rehash("garbage")
    finally:
        security.restore_password_hasher(previous)


def test_default_hasher_is_argon2id_rfc9106() -> None:
    h = security.hash_password("x" * 12)
    assert h.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


@pytest.mark.parametrize("bad", ["short", " leading-space-pw", "x" * 300])
def test_validate_password_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        security.validate_password(bad)


def test_tokens() -> None:
    token, token_hash = security.new_session_token()
    assert len(token) >= 43 and token_hash == security.hash_token(token) and len(token_hash) == 32
    issued = security.new_api_token()
    assert issued.token.startswith("ast_") and security.is_api_token(issued.token)
    assert issued.prefix == issued.token[:12] and issued.token_hash == security.hash_token(issued.token)
    assert security.new_csrf_token() != security.new_csrf_token()
    assert security.constant_time_equals("a", "a") and not security.constant_time_equals("a", "b")
    assert not security.constant_time_equals(None, None)


def test_visitor_key_and_ip_hash() -> None:
    k1 = security.visitor_key("salt", date(2026, 9, 26), "1.2.3.4", "UA")
    assert k1 == security.visitor_key("salt", "2026-09-26", "1.2.3.4", "UA") and len(k1) == 32
    assert k1 != security.visitor_key("salt", date(2026, 9, 27), "1.2.3.4", "UA")
    assert len(security.ip_hash("1.2.3.4", "salt")) == 32 and "1.2.3.4" not in security.ip_hash(
        "1.2.3.4", "s"
    )


def test_secret_cipher_and_rotation(tmp_path: Path) -> None:
    old_key, new_key = security.generate_fernet_key(), security.generate_fernet_key()
    old = security.SecretCipher(security.parse_fernet_keys(old_key))
    ct, kid = old.encrypt("sk-live-секрет")
    assert kid == old.primary_key_id and b"sk-live" not in ct

    both = security.SecretCipher(security.parse_fernet_keys(f"{new_key}, {old_key}"))
    assert both.decrypt(ct) == "sk-live-секрет"
    rotated, new_kid = both.rotate(ct)
    assert new_kid == security.secret_key_id(new_key.encode()) != kid
    only_new = security.SecretCipher(security.parse_fernet_keys(new_key))
    assert only_new.decrypt(rotated) == "sk-live-секрет"
    with pytest.raises(errors.ConfigError):
        only_new.decrypt(ct)


def test_secret_keys_from_env_and_file(monkeypatch, tmp_path: Path) -> None:
    key = security.generate_fernet_key()
    keyfile = tmp_path / "keys"
    keyfile.write_text(f"# основной\n{key}\n", encoding="utf-8")
    monkeypatch.delenv("AS_SECRETS_KEY", raising=False)
    env = _env(monkeypatch, AS_SECRETS_KEY_FILE=str(keyfile))
    assert security.load_secret_keys(env) == [key.encode()]
    monkeypatch.delenv("AS_SECRETS_KEY_FILE")
    with pytest.raises(errors.ConfigError, match="AS_SECRETS_KEY"):
        security.load_secret_keys(EnvSettings(_env_file=None))
    with pytest.raises(errors.ConfigError, match="Fernet"):
        security.parse_fernet_keys("not-a-key")


def test_last4() -> None:
    assert security.last4("sk-1234567890abcd") == "abcd"
    assert security.last4("short") is None and security.last4(None) is None


# ---------------------------------------------------------------- ids


def test_package_codes() -> None:
    codes = {ids.new_package_code() for _ in range(3000)}
    assert len(codes) > 2990
    for code in codes:
        assert len(code) == 5 and ids.is_package_code(code) and ids.is_telegram_start_param(code)
        assert not set(code) & set("01oliu") and code == code.lower()
    assert set("".join(codes)) == set(ids.PACKAGE_CODE_ALPHABET)
    assert not ids.is_package_code("k7m2o") and not ids.is_package_code("K7M2Q")


def test_crockford_roundtrip_and_aliases() -> None:
    for n in (0, 1, 31, 32, 1234567, 2**80 - 1):
        assert ids.crockford_decode(ids.crockford_encode(n)) == n
    assert ids.crockford_encode(5, min_length=4) == "0005"
    assert ids.crockford_decode("O-I-L") == ids.crockford_decode("011")
    assert ids.crockford_decode("ZZ") == ids.crockford_decode("zz") == 1023
    with pytest.raises(ValueError):
        ids.crockford_decode("u1")
    with pytest.raises(ValueError):
        ids.crockford_encode(-1)


def test_request_and_worker_ids() -> None:
    rid = ids.new_request_id()
    assert len(rid) == 16 and set(rid) <= set(ids.CROCKFORD_ALPHABET)
    wid = ids.new_worker_id("heavy")
    assert wid.endswith("-heavy") and str(__import__("os").getpid()) in wid


# ---------------------------------------------------------------- timeutil


def test_isoformat_and_parse() -> None:
    dt = datetime(2026, 9, 26, 19, 0, 5, 123456, tzinfo=UTC)
    assert timeutil.isoformat_z(dt) == "2026-09-26T19:00:05Z"
    assert timeutil.isoformat_z(None) is None
    assert timeutil.parse_datetime("2026-09-26T22:00:05+03:00") == dt.replace(microsecond=0)
    assert timeutil.parse_datetime("2026-09-26T19:00:05Z").tzinfo is UTC
    assert timeutil.ensure_utc(datetime(2026, 1, 1)).tzinfo is UTC


def test_business_day_moscow() -> None:
    late_utc = datetime(2026, 9, 26, 21, 30, tzinfo=UTC)  # 00:30 МСК следующего дня
    assert timeutil.business_day(late_utc) == date(2026, 9, 27)
    assert timeutil.business_day(late_utc, "UTC") == date(2026, 9, 26)
    start, end = timeutil.day_bounds_utc(date(2026, 9, 27))
    assert start == datetime(2026, 9, 26, 21, 0, tzinfo=UTC) and end - start == timedelta(days=1)
    with pytest.raises(ValueError):
        timeutil.zone("Mars/Olympus")


def test_date_range_inclusive() -> None:
    start, end = timeutil.date_range_utc(date(2026, 9, 1), date(2026, 9, 30))
    assert start == datetime(2026, 8, 31, 21, 0, tzinfo=UTC)
    assert end == datetime(2026, 9, 30, 21, 0, tzinfo=UTC)
    assert timeutil.date_range_utc(None, None) == (None, None)
    with pytest.raises(ValueError):
        timeutil.date_range_utc(date(2026, 9, 2), date(2026, 9, 1))


def test_next_local_time() -> None:
    after = datetime(2026, 9, 26, 0, 30, tzinfo=UTC)  # 03:30 МСК
    assert timeutil.next_local_time_utc(time(4, 0), after=after) == datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
    after2 = datetime(2026, 9, 26, 1, 0, tzinfo=UTC)  # ровно 04:00 МСК -> следующий день
    assert timeutil.next_local_time_utc(time(4, 0), after=after2) == datetime(2026, 9, 27, 1, 0, tzinfo=UTC)
    assert timeutil.age_hours(after, after2) == pytest.approx(0.5)
    assert "CAST(%(tz)s AS text)" in timeutil.BUSINESS_DAY_SQL


# ---------------------------------------------------------------- logging


def test_redact_structures_and_text() -> None:
    data = {
        "FAL_API_KEY": "fal-123",
        "nested": {"password": "p", "items": [{"token": "t"}]},
        "dedup_key": "job:1:voice:0",
        "note": "db postgresql://u:hunter2@h/db, Authorization: Bearer abc.def and ast_abcdefghijklmnop",
    }
    red = aslog.redact(data)
    assert red["FAL_API_KEY"] == red["nested"]["password"] == red["nested"]["items"][0]["token"] == "***"
    assert red["dedup_key"] == "job:1:voice:0"
    for leaked in ("hunter2", "abc.def", "ast_abcdefghijklmnop"):
        assert leaked not in red["note"]
    assert data["FAL_API_KEY"] == "fal-123"  # оригинал не тронут
    assert "LLM_API_KEY=***" in aslog.redact_text("LLM_API_KEY=sk-live-1 retry")


def _capture(fmt: str) -> tuple[io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = aslog.configure_logging("DEBUG", fmt, stream=stream)
    return stream, handler


def test_json_logging_redacts_and_adds_context() -> None:
    stream, handler = _capture("json")
    try:
        log = aslog.get_logger("autoshorts.test", worker_id="w1")
        with aslog.log_context(request_id="r1", task_id=7):
            log.info("ключ FAL_API_KEY=fal-secret", extra={"api_token": "ast_zzzzzzzzzzzz", "stage": "voice"})
        try:
            raise RuntimeError("connect postgresql://u:hunter2@h/db")
        except RuntimeError:
            logging.getLogger("autoshorts.test").exception("fail")
    finally:
        logging.getLogger().removeHandler(handler)
    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert first["level"] == "INFO" and first["logger"] == "autoshorts.test"
    assert first["request_id"] == "r1" and first["task_id"] == 7 and first["worker_id"] == "w1"
    assert first["stage"] == "voice" and first["api_token"] == "***"
    assert "fal-secret" not in first["message"]
    assert "hunter2" not in json.dumps(second) and "RuntimeError" in json.dumps(second)


def test_text_logging_redacts_args() -> None:
    stream, handler = _capture("text")
    try:
        logging.getLogger("autoshorts.test").warning("cfg %s", {"secret": "s3cr3t", "ok": 1})
    finally:
        logging.getLogger().removeHandler(handler)
    out = stream.getvalue()
    assert "s3cr3t" not in out and "'ok': 1" in out


def test_context_is_scoped() -> None:
    with aslog.log_context(a=1):
        with aslog.log_context(b=2):
            assert aslog.current_context() == {"a": 1, "b": 2}
        assert aslog.current_context() == {"a": 1}
    assert aslog.current_context() == {}


# ---------------------------------------------------------------- errors


def test_error_payloads() -> None:
    assert errors.NotFound("job").to_payload() == {
        "error": {"code": "job_not_found", "message": "Не найдено", "details": {}}
    }
    exc = errors.ValidationFailed(details={"errors": [{"loc": ["body", "x"], "msg": "m", "type": "t"}]})
    assert exc.status == 422 and exc.code == "validation_error"
    assert errors.CsrfFailed().status == 403 and errors.CsrfFailed().code == "csrf_failed"
    assert (
        errors.MigrationsPending().code == "migrations_pending" and errors.MigrationsPending().status == 503
    )
    assert errors.BudgetPaused().status == 423 and errors.InsufficientStorage().status == 507
    assert errors.Conflict(code="publication_exists").code == "publication_exists"
    deferred = errors.ResourceDeferred("disk_low", retry_after_sec=30)
    assert isinstance(deferred, errors.TaskError) and deferred.reason == "disk_low"


def test_error_codes_are_in_contract() -> None:
    from autoshorts.api.schemas.common import ERROR_CODES

    known = {code for codes in ERROR_CODES.values() for code in codes}
    for cls in (
        errors.BadRequest, errors.NotAuthenticated, errors.Forbidden, errors.CsrfFailed,
        errors.JobStateConflict, errors.VersionConflict, errors.PayloadTooLarge, errors.UnsupportedMediaType,
        errors.ValidationFailed, errors.BudgetPaused, errors.RateLimited, errors.NotReady,
        errors.MigrationsPending, errors.InsufficientStorage,
    ):  # fmt: skip
        assert cls.code in known, cls
        assert cls.code in ERROR_CODES[cls.status], cls
