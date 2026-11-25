import hashlib
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import config

logger = config.logger

# ---------------------------------------------------------------------------
# SQLite Event / Item Persistence
# ---------------------------------------------------------------------------

class SessionStore:
    """Stores structured chat items in SQLite so mobile reconnects restore instantly."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_items_session_id ON items(session_id)
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                workdir TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                profile TEXT NOT NULL DEFAULT 'agy2'
            )
            """
        )
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "profile" not in cols:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN profile TEXT NOT NULL DEFAULT 'agy2'"
            )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS passkeys (
                credential_id TEXT PRIMARY KEY,
                public_key_pem TEXT NOT NULL,
                alg INTEGER NOT NULL,
                sign_count INTEGER NOT NULL DEFAULT 0,
                label TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                last_used_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

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

    def upsert_session(
        self,
        session_id: str,
        workdir: str,
        title: str,
        updated_at: Optional[float] = None,
        touch: bool = False,
        profile: str = "",
    ) -> None:
        if not session_id:
            return
        prof = profile or config.DEFAULT_AGY_PROFILE
        now = time.time()
        if touch:
            ts = float(updated_at) if updated_at is not None else now
            self._conn.execute(
                """
                INSERT INTO sessions (session_id, workdir, title, created_at, updated_at, profile)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    workdir = excluded.workdir,
                    title = excluded.title,
                    updated_at = excluded.updated_at,
                    profile = CASE WHEN excluded.profile != '' THEN excluded.profile ELSE sessions.profile END
                """,
                (session_id, workdir, title or "新会话", now, ts, prof),
            )
        else:
            ts = float(updated_at) if updated_at is not None else 0.0
            self._conn.execute(
                """
                INSERT INTO sessions (session_id, workdir, title, created_at, updated_at, profile)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    workdir = excluded.workdir,
                    title = excluded.title,
                    profile = CASE WHEN excluded.profile != '' THEN excluded.profile ELSE sessions.profile END,
                    updated_at = CASE
                        WHEN excluded.updated_at > 0 AND sessions.updated_at <= 0
                            THEN excluded.updated_at
                        ELSE sessions.updated_at
                    END
                """,
                (session_id, workdir, title or "新会话", now, ts, prof),
            )
        self._conn.commit()

    def touch_session(self, session_id: str, ts: Optional[float] = None) -> None:
        if not session_id:
            return
        val = float(ts) if ts is not None else time.time()
        self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (val, session_id),
        )
        self._conn.commit()

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT session_id, workdir, title, created_at, updated_at FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "session_id": row[0],
            "workdir": row[1],
            "title": row[2],
            "created_at": row[3],
            "updated_at": row[4],
        }

    def list_sessions(self, limit: int = 100, profile: Optional[str] = None) -> List[Dict[str, Any]]:
        if profile:
            rows = self._conn.execute(
                """
                SELECT s.session_id, s.workdir, s.title, s.created_at, s.updated_at,
                       (SELECT COUNT(*) FROM items i WHERE i.session_id = s.session_id) AS item_count
                FROM sessions s
                WHERE s.profile = ?
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (profile, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT s.session_id, s.workdir, s.title, s.created_at, s.updated_at,
                       (SELECT COUNT(*) FROM items i WHERE i.session_id = s.session_id) AS item_count
                FROM sessions s
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "session_id": r[0],
                "workdir": r[1],
                "title": r[2],
                "created_at": r[3],
                "updated_at": r[4],
                "item_count": r[5],
            }
            for r in rows
        ]

    def count_items(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM items WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    def get_sessions_with_items(self) -> Set[str]:
        rows = self._conn.execute("SELECT DISTINCT session_id FROM items").fetchall()
        return {str(r[0]) for r in rows if r and r[0]}

    def delete_session(self, session_id: str) -> None:
        if not session_id:
            return
        with self._conn:
            self._conn.execute("DELETE FROM items WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    def set_meta(self, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self._conn.commit()

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except Exception:
            return default

    def upsert_item(self, session_id: str, item: Dict[str, Any]) -> None:
        now = time.time()
        item_id = item["id"]
        item_type = item["type"]
        created_at = item.get("ts", now)
        payload = json.dumps(item, ensure_ascii=False)
        self._conn.execute(
            """
            INSERT INTO items (item_id, session_id, item_type, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (item_id, session_id, item_type, payload, created_at, now),
        )
        self._conn.commit()

    def load_items(self, session_id: Optional[str] = None, limit: int = 2000) -> List[Dict[str, Any]]:
        if session_id:
            rows = self._conn.execute(
                """
                SELECT payload FROM (
                    SELECT seq, payload FROM items WHERE session_id = ? ORDER BY seq DESC LIMIT ?
                ) ORDER BY seq ASC
                """,
                (session_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT payload FROM (
                    SELECT seq, payload FROM items ORDER BY seq DESC LIMIT ?
                ) ORDER BY seq ASC
                """,
                (limit,),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for (payload_str,) in rows:
            try:
                result.append(json.loads(payload_str))
            except Exception:
                continue
        return result

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT payload FROM items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def clear_session(self, session_id: Optional[str] = None) -> None:
        if session_id:
            self._conn.execute("DELETE FROM items WHERE session_id = ?", (session_id,))
        else:
            self._conn.execute("DELETE FROM items")
        self._conn.commit()

    def replace_session_items(self, session_id: str, items: List[Dict[str, Any]]) -> None:
        now = time.time()
        with self._conn:
            self._conn.execute("DELETE FROM items WHERE session_id = ?", (session_id,))
            rows = []
            for it in items:
                created_at = float(it.get("ts", now))
                rows.append(
                    (
                        it["id"],
                        session_id,
                        it["type"],
                        json.dumps(it, ensure_ascii=False),
                        created_at,
                        now,
                    )
                )
            if rows:
                self._conn.executemany(
                    """
                    INSERT OR REPLACE INTO items
                        (item_id, session_id, item_type, payload, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

    @staticmethod
    def _extract_item_search_text(item: Dict[str, Any]) -> str:
        item_type = str(item.get("type") or "")
        if item_type == "tool":
            parts: List[str] = []
            for key in ("title", "name", "args", "output"):
                val = item.get(key)
                if val is None or val == "":
                    continue
                if isinstance(val, (dict, list)):
                    try:
                        s = json.dumps(val, ensure_ascii=False)
                    except Exception:
                        s = str(val)
                else:
                    s = str(val)
                if s:
                    parts.append(s)
            return "\n".join(parts)
        return str(item.get("text") or "")

    @staticmethod
    def _build_match_snippet(text: str, terms: List[str], max_len: int = 120) -> str:
        collapsed = re.sub(r"\s+", " ", str(text or "")).strip()
        if not collapsed:
            return ""
        lower = collapsed.lower()
        idx = -1
        for term in terms:
            pos = lower.find(term)
            if pos >= 0:
                idx = pos
                break
        if idx < 0:
            return collapsed[:max_len] + ("…" if len(collapsed) > max_len else "")
        start = max(0, idx - 32)
        end = min(len(collapsed), start + max_len)
        if end - start < max_len and start > 0:
            start = max(0, end - max_len)
        snippet = collapsed[start:end].strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(collapsed):
            snippet = snippet + "…"
        return snippet

    def search_sessions(
        self,
        profile: str,
        query: str,
        *,
        limit: int = 50,
        excluded_session_ids: Optional[Set[str]] = None,
        only_session_ids: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Full-text search sessions & message items strictly within a single profile."""
        prof = str(profile or "").strip()
        if not prof:
            return []
        raw_q = str(query or "").strip()
        if not raw_q:
            return []
        terms = [t.lower() for t in raw_q.split() if t][:6]
        if not terms:
            return []
        if only_session_ids is not None and not only_session_ids:
            return []

        max_results = max(1, min(int(limit or 50), 200))
        excluded = excluded_session_ids or set()
        priority_map = {
            "user": 0,
            "assistant": 1,
            "error": 2,
            "tool": 3,
            "thought": 4,
        }

        use_separate_conn = str(self.db_path) != ":memory:"
        conn = sqlite3.connect(str(self.db_path)) if use_separate_conn else self._conn
        try:
            if use_separate_conn:
                conn.execute("PRAGMA query_only = ON;")

            sess_rows = conn.execute(
                """
                SELECT session_id, workdir, title, created_at, updated_at
                FROM sessions
                WHERE profile = ?
                ORDER BY updated_at DESC
                """,
                (prof,),
            ).fetchall()

            sessions_by_id: Dict[str, Dict[str, Any]] = {}
            matches: Dict[str, Dict[str, Any]] = {}
            for sid, workdir, title, created_at, updated_at in sess_rows:
                if sid in excluded:
                    continue
                if only_session_ids is not None and sid not in only_session_ids:
                    continue
                info = {
                    "session_id": sid,
                    "workdir": workdir or "",
                    "title": title or "新会话",
                    "created_at": float(created_at or 0.0),
                    "updated_at": float(updated_at or 0.0),
                }
                sessions_by_id[sid] = info
                haystack = f"{info['title']}\n{info['workdir']}".lower()
                if all(t in haystack for t in terms):
                    matches[sid] = {
                        **info,
                        "matched_in": "title",
                        "match_count": 0,
                        "snippet": "",
                        "match_type": "",
                        "match_item_id": "",
                        "match_ts": 0.0,
                        "_prio": 99,
                    }

            if not sessions_by_id:
                return []

            like_clauses: List[str] = []
            params: List[Any] = [prof]
            for term in terms:
                if '"' in term or "\\" in term:
                    continue
                escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                like_clauses.append("i.payload LIKE ? ESCAPE '\\'")
                params.append(f"%{escaped}%")

            where_sql = "s.profile = ?"
            if like_clauses:
                where_sql += " AND " + " AND ".join(like_clauses)

            cur = conn.execute(
                f"""
                SELECT i.session_id, i.item_id, i.item_type, i.payload, i.created_at
                FROM items i
                INNER JOIN sessions s ON s.session_id = i.session_id
                WHERE {where_sql}
                ORDER BY s.updated_at DESC,
                         CASE i.item_type
                             WHEN 'user' THEN 0
                             WHEN 'assistant' THEN 1
                             WHEN 'error' THEN 2
                             WHEN 'tool' THEN 3
                             ELSE 4
                         END ASC,
                         i.created_at DESC
                """,
                params,
            )

            per_session_cap = 50
            for sid, item_id, item_type, payload_str, created_at in cur:
                info = sessions_by_id.get(sid)
                if info is None:
                    continue
                existing = matches.get(sid)
                if existing is not None and existing["match_count"] >= per_session_cap:
                    continue
                try:
                    item_obj = json.loads(payload_str)
                except Exception:
                    continue
                text = self._extract_item_search_text(item_obj)
                if not text:
                    continue
                lower_text = text.lower()
                if not all(t in lower_text for t in terms):
                    continue

                prio = priority_map.get(str(item_type or ""), 5)
                if existing is None:
                    matches[sid] = {
                        **info,
                        "matched_in": "content",
                        "match_count": 1,
                        "snippet": self._build_match_snippet(text, terms),
                        "match_type": str(item_type or ""),
                        "match_item_id": str(item_id or ""),
                        "match_ts": float(created_at or 0.0),
                        "_prio": prio,
                    }
                else:
                    if existing["matched_in"] == "title":
                        existing["matched_in"] = "both"
                    existing["match_count"] += 1
                    if prio < existing["_prio"] or not existing["snippet"]:
                        existing["snippet"] = self._build_match_snippet(text, terms)
                        existing["match_type"] = str(item_type or "")
                        existing["match_item_id"] = str(item_id or "")
                        existing["match_ts"] = float(created_at or 0.0)
                        existing["_prio"] = prio

            ordered = sorted(
                matches.values(),
                key=lambda r: (
                    -(float(r.get("updated_at") or 0.0)),
                    -(int(r.get("match_count") or 0)),
                ),
            )[:max_results]
            for r in ordered:
                r.pop("_prio", None)
            return ordered
        finally:
            if use_separate_conn:
                conn.close()




def ensure_migrated_db_path() -> Path:
    if not config.DB_PATH.exists() and config.LEGACY_DB_PATH.exists():
        try:
            for suffix in ("", "-wal", "-shm"):
                src = Path(f"{config.LEGACY_DB_PATH}{suffix}")
                dst = Path(f"{config.DB_PATH}{suffix}")
                if src.exists() and not dst.exists():
                    src.rename(dst)
            logger.info("Migrated legacy SQLite database %s -> %s", config.LEGACY_DB_PATH.name, config.DB_PATH.name)
        except OSError as exc:
            logger.warning("Could not migrate legacy DB %s (%s); falling back to legacy path", config.LEGACY_DB_PATH, exc)
            return config.LEGACY_DB_PATH
    return config.DB_PATH


