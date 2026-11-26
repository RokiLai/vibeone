"""Auth session and WebAuthn passkey persistence mixin for SessionStore."""

import hashlib
import secrets
import time
from typing import Any, Dict, List, Optional


class StoreAuthMixin:
    """Auth sessions and passkey CRUD operations backed by SessionStore._conn."""

    @staticmethod
    def _hash_session_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def create_auth_session(self, ttl_seconds: int = 60 * 60 * 24 * 30) -> str:
        now = time.time()
        raw_token = f"v1s_{secrets.token_urlsafe(32)}"
        token_hash = self._hash_session_token(raw_token)
        self._conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
        self._conn.execute(
            "INSERT INTO auth_sessions (token_hash, created_at, expires_at) VALUES (?, ?, ?)",
            (token_hash, now, now + ttl_seconds),
        )
        self._conn.commit()
        return raw_token

    def validate_auth_session(self, raw_token: str) -> bool:
        if not raw_token or not raw_token.startswith("v1s_"):
            return False
        now = time.time()
        token_hash = self._hash_session_token(raw_token)
        row = self._conn.execute(
            "SELECT expires_at FROM auth_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if not row:
            return False
        if float(row[0]) <= now:
            self._conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
            self._conn.commit()
            return False
        return True

    def revoke_auth_session(self, raw_token: Optional[str]) -> None:
        if not raw_token or not raw_token.startswith("v1s_"):
            return
        token_hash = self._hash_session_token(raw_token)
        self._conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
        self._conn.commit()

    def add_passkey(
        self,
        credential_id: str,
        public_key_pem: str,
        alg: int,
        sign_count: int = 0,
        label: str = "",
    ) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO passkeys (credential_id, public_key_pem, alg, sign_count, label, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(credential_id) DO UPDATE SET
                public_key_pem = excluded.public_key_pem,
                alg = excluded.alg,
                sign_count = excluded.sign_count,
                label = CASE WHEN excluded.label != '' THEN excluded.label ELSE passkeys.label END,
                last_used_at = excluded.last_used_at
            """,
            (credential_id, public_key_pem, alg, int(sign_count), label.strip()[:80], now, now),
        )
        self._conn.commit()

    def get_passkey(self, credential_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            """
            SELECT credential_id, public_key_pem, alg, sign_count, label, created_at, last_used_at
            FROM passkeys
            WHERE credential_id = ?
            """,
            (credential_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "credential_id": row[0],
            "public_key_pem": row[1],
            "alg": int(row[2]),
            "sign_count": int(row[3]),
            "label": row[4],
            "created_at": float(row[5]),
            "last_used_at": float(row[6]),
        }

    def list_passkeys(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT credential_id, alg, sign_count, label, created_at, last_used_at
            FROM passkeys
            ORDER BY created_at ASC
            """
        ).fetchall()
        return [
            {
                "credential_id": r[0],
                "alg": int(r[1]),
                "sign_count": int(r[2]),
                "label": r[3],
                "created_at": float(r[4]),
                "last_used_at": float(r[5]),
            }
            for r in rows
        ]

    def count_passkeys(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM passkeys").fetchone()
        return int(row[0]) if row else 0

    def update_passkey_usage(self, credential_id: str, sign_count: int) -> None:
        self._conn.execute(
            "UPDATE passkeys SET sign_count = ?, last_used_at = ? WHERE credential_id = ?",
            (int(sign_count), time.time(), credential_id),
        )
        self._conn.commit()

    def delete_passkey(self, credential_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM passkeys WHERE credential_id = ?",
            (credential_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0
