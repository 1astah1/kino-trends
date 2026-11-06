"""Таблица `secrets` (миграция 0002): значения зашифрованы Fernet, в API не отдаются никогда."""

from __future__ import annotations

from typing import Any

Row = dict[str, Any]


def list_rows(conn: Any) -> list[Row]:
    """Все записи без шифртекста."""
    return conn.execute(
        "SELECT name, key_id, last4, updated_by, updated_at FROM secrets ORDER BY name"
    ).fetchall()


def get_ciphertext(conn: Any, name: str) -> Row | None:
    return conn.execute(
        "SELECT name, ciphertext, key_id, updated_at FROM secrets WHERE name = %(name)s", {"name": name}
    ).fetchone()


def upsert(
    conn: Any, *, name: str, ciphertext: bytes, key_id: str, last4: str | None, updated_by: int | None
) -> bool:
    """True — запись создана, False — обновлена."""
    row = conn.execute(
        """
        INSERT INTO secrets (name, ciphertext, key_id, last4, updated_by, updated_at)
        VALUES (%(name)s, %(ct)s, %(key_id)s, %(last4)s, %(by)s, now())
        ON CONFLICT (name) DO UPDATE
          SET ciphertext = EXCLUDED.ciphertext, key_id = EXCLUDED.key_id, last4 = EXCLUDED.last4,
              updated_by = EXCLUDED.updated_by, updated_at = now()
        RETURNING (xmax = 0) AS inserted
        """,
        {"name": name, "ct": ciphertext, "key_id": key_id, "last4": last4, "by": updated_by},
    ).fetchone()
    return bool(row["inserted"])


def delete(conn: Any, name: str) -> bool:
    return conn.execute("DELETE FROM secrets WHERE name = %(name)s", {"name": name}).rowcount > 0


def references(conn: Any) -> list[Row]:
    """Кто ссылается на секреты: `{secret_name, ref}`; ref — ключ провайдера или `product:<id>` и т. п."""
    return conn.execute(
        """
        SELECT secret_name, ref FROM (
          SELECT secret_name, key AS ref, 1 AS ord FROM provider_configs WHERE secret_name IS NOT NULL
          UNION ALL
          SELECT webhook_secret_name, 'product:' || id, 2 FROM products WHERE webhook_secret_name IS NOT NULL
          UNION ALL
          SELECT youtube_api_key_secret, 'cluster:' || id, 3 FROM clusters
           WHERE youtube_api_key_secret IS NOT NULL
          UNION ALL
          SELECT youtube_oauth_client_secret, 'cluster:' || id, 3 FROM clusters
           WHERE youtube_oauth_client_secret IS NOT NULL
          UNION ALL
          SELECT refresh_token_secret, 'channel:' || channel_id, 4 FROM channel_oauth
        ) r
        ORDER BY secret_name, ord, ref
        """
    ).fetchall()
