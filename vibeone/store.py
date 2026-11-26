import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import config
from .store_auth import StoreAuthMixin
from .store_search import StoreSearchMixin

logger = config.logger

# ---------------------------------------------------------------------------
# SQLite Event / Item Persistence
# ---------------------------------------------------------------------------


class SessionStore(StoreAuthMixin, StoreSearchMixin):
    """Stores structured chat items in SQLite so mobile reconnects restore instantly."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._sessions_with_items_cache: Optional[Set[str]] = None
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
                profile TEXT NOT NULL DEFAULT 'default'
            )
            """
        )
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "profile" not in cols:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN profile TEXT NOT NULL DEFAULT 'default'"
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
        self._fts5_enabled = False
        self._init_fts5()
        self._conn.commit()

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
        if self._sessions_with_items_cache is None:
            rows = self._conn.execute("SELECT DISTINCT session_id FROM items").fetchall()
            self._sessions_with_items_cache = {str(r[0]) for r in rows if r and r[0]}
        return set(self._sessions_with_items_cache)

    def delete_session(self, session_id: str) -> None:
        if not session_id:
            return
        with self._conn:
            cur = self._conn.execute("DELETE FROM items WHERE session_id = ?", (session_id,))
            if self._fts5_enabled and cur.rowcount > 0:
                self._conn.execute("DELETE FROM items_fts WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        if self._sessions_with_items_cache is not None:
            self._sessions_with_items_cache.discard(session_id)

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
        self._upsert_fts5_item(session_id, item)
        self._conn.commit()
        if self._sessions_with_items_cache is not None and session_id:
            self._sessions_with_items_cache.add(session_id)

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

    def get_referenced_upload_filenames(self) -> Set[str]:
        """Return all upload filenames currently referenced by stored items or metadata."""
        refs: Set[str] = set()
        upload_re = re.compile(r"/uploads/([A-Za-z0-9._-]+)")
        use_separate_conn = str(self.db_path) != ":memory:"
        conn = sqlite3.connect(str(self.db_path)) if use_separate_conn else self._conn
        try:
            if use_separate_conn:
                conn.execute("PRAGMA query_only = ON;")
            for (payload_str,) in conn.execute(
                "SELECT payload FROM items WHERE payload LIKE '%/uploads/%'"
            ).fetchall():
                if not payload_str:
                    continue
                refs.update(upload_re.findall(payload_str))
                try:
                    obj = json.loads(payload_str)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    imgs = obj.get("images")
                    if isinstance(imgs, list):
                        for img in imgs:
                            if isinstance(img, dict):
                                fname = str(img.get("filename") or "").strip()
                                if fname and "/" not in fname and "\\" not in fname and ".." not in fname:
                                    refs.add(fname)
            for (val_str,) in conn.execute(
                "SELECT value FROM meta WHERE value LIKE '%/uploads/%'"
            ).fetchall():
                if val_str:
                    refs.update(upload_re.findall(val_str))
            return refs
        finally:
            if use_separate_conn:
                conn.close()

    def clear_session(self, session_id: Optional[str] = None) -> None:
        if session_id:
            cur = self._conn.execute("DELETE FROM items WHERE session_id = ?", (session_id,))
            if self._fts5_enabled and cur.rowcount > 0:
                self._conn.execute("DELETE FROM items_fts WHERE session_id = ?", (session_id,))
            if self._sessions_with_items_cache is not None:
                self._sessions_with_items_cache.discard(session_id)
        else:
            if self._fts5_enabled:
                self._conn.execute("DELETE FROM items_fts")
            self._conn.execute("DELETE FROM items")
            if self._sessions_with_items_cache is not None:
                self._sessions_with_items_cache.clear()
        self._conn.commit()

    def replace_session_items(self, session_id: str, items: List[Dict[str, Any]]) -> None:
        now = time.time()
        with self._conn:
            cur = self._conn.execute("DELETE FROM items WHERE session_id = ?", (session_id,))
            if self._fts5_enabled and cur.rowcount > 0:
                self._conn.execute("DELETE FROM items_fts WHERE session_id = ?", (session_id,))
            rows = []
            fts_rows = []
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
                if self._fts5_enabled:
                    text = self._extract_item_search_text(it)
                    if text:
                        fts_rows.append((it["id"], session_id, it["type"], text))
            if rows:
                self._conn.executemany(
                    """
                    INSERT OR REPLACE INTO items
                        (item_id, session_id, item_type, payload, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            if fts_rows:
                self._conn.executemany(
                    "INSERT INTO items_fts (item_id, session_id, item_type, content) VALUES (?, ?, ?, ?)",
                    fts_rows,
                )
        if self._sessions_with_items_cache is not None and session_id:
            if rows:
                self._sessions_with_items_cache.add(session_id)
            else:
                self._sessions_with_items_cache.discard(session_id)


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
