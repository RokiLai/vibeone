"""FTS5 indexing and full-text session search mixin for SessionStore."""

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set

from . import config

logger = config.logger


_FTS5_SCHEMA_VERSION = 2
_FTS_MAX_TOOL_ARGS_CHARS = 2048
_FTS_MAX_TOOL_OUTPUT_CHARS = 4096


class StoreSearchMixin:
    """FTS5 trigram indexing and full-text search across sessions and items."""

    def _create_fts5_table(self) -> bool:
        """Create items_fts using contentless_delete=1 when supported, else standard FTS5."""
        try:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                    content,
                    content='',
                    contentless_delete=1,
                    tokenize='trigram'
                )
                """
            )
            self._fts5_contentless = True
            return True
        except sqlite3.OperationalError:
            self._fts5_contentless = False
        try:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                    content,
                    tokenize='trigram'
                )
                """
            )
            return True
        except sqlite3.OperationalError as exc:
            logger.warning("SQLite FTS5 trigram unavailable, falling back to LIKE search: %s", exc)
            return False

    def _clear_fts5_table(self) -> None:
        if not self._fts5_enabled:
            return
        if getattr(self, "_fts5_contentless", False):
            self._conn.execute("INSERT INTO items_fts(items_fts) VALUES('delete-all')")
        else:
            self._conn.execute("DELETE FROM items_fts")

    def optimize_fts5(self) -> None:
        """Merge FTS5 b-tree segments to reclaim space and keep search fast."""
        if not self._fts5_enabled:
            return
        try:
            self._conn.execute("INSERT INTO items_fts(items_fts) VALUES('optimize')")
            self._conn.commit()
        except sqlite3.OperationalError as exc:
            logger.warning("SQLite FTS5 optimize failed: %s", exc)

    def _init_fts5(self) -> None:
        """Initialize FTS5 trigram index for items and migrate/backfill when schema version changes."""
        self._fts5_contentless = False
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'fts5_items_version'").fetchone()
        version = None
        if row:
            try:
                version = json.loads(row[0])
            except Exception:
                version = None

        if version != _FTS5_SCHEMA_VERSION:
            self._conn.execute("DROP TABLE IF EXISTS items_fts")
            self._fts5_enabled = self._create_fts5_table()
            if not self._fts5_enabled:
                return
            self._rebuild_fts5_index()
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('fts5_items_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(_FTS5_SCHEMA_VERSION),),
            )
            return

        # Already on current schema version: detect whether existing table is contentless
        ddl_row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'items_fts'"
        ).fetchone()
        if ddl_row and ddl_row[0]:
            self._fts5_enabled = True
            self._fts5_contentless = "content=''" in str(ddl_row[0]).lower().replace(" ", "")
        else:
            self._fts5_enabled = self._create_fts5_table()
            if self._fts5_enabled:
                self._rebuild_fts5_index()

    def _rebuild_fts5_index(self) -> None:
        if not self._fts5_enabled:
            return
        self._clear_fts5_table()
        rows = self._conn.execute(
            "SELECT seq, payload FROM items"
        ).fetchall()
        fts_rows = []
        for seq, payload_str in rows:
            try:
                obj = json.loads(payload_str)
            except Exception:
                continue
            text = self._extract_item_search_text(obj, for_index=True) if isinstance(obj, dict) else ""
            if text:
                fts_rows.append((seq, text))
        if fts_rows:
            self._conn.executemany(
                "INSERT INTO items_fts (rowid, content) VALUES (?, ?)",
                fts_rows,
            )
            self._conn.execute("INSERT INTO items_fts(items_fts) VALUES('optimize')")

    def _upsert_fts5_item(self, seq: int, item: Dict[str, Any]) -> None:
        if not self._fts5_enabled:
            return
        self._conn.execute("DELETE FROM items_fts WHERE rowid = ?", (seq,))
        text = self._extract_item_search_text(item, for_index=True)
        if text:
            self._conn.execute(
                "INSERT INTO items_fts (rowid, content) VALUES (?, ?)",
                (seq, text),
            )

    @staticmethod
    def _extract_item_search_text(item: Dict[str, Any], *, for_index: bool = False) -> str:
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
                if for_index:
                    if key == "output" and len(s) > _FTS_MAX_TOOL_OUTPUT_CHARS:
                        s = s[:_FTS_MAX_TOOL_OUTPUT_CHARS]
                    elif key == "args" and len(s) > _FTS_MAX_TOOL_ARGS_CHARS:
                        s = s[:_FTS_MAX_TOOL_ARGS_CHARS]
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

            per_session_cap = 50
            fts_terms = [t for t in terms if len(t) >= 3]
            short_terms = [t for t in terms if len(t) < 3]

            def _record_hit(sid: str, item_id: str, item_type: str, text: str, created_at: float) -> None:
                info = sessions_by_id.get(sid)
                if info is None or not text:
                    return
                existing = matches.get(sid)
                if existing is not None and existing["match_count"] >= per_session_cap:
                    return
                lower_text = text.lower()
                if not all(t in lower_text for t in terms):
                    return

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

            if self._fts5_enabled and fts_terms:
                where_parts: List[str] = ["s.profile = ?"]
                params: List[Any] = [prof]
                match_expr = " AND ".join(
                    '"' + t.replace('"', '""') + '"' for t in fts_terms
                )
                where_parts.append("items_fts MATCH ?")
                params.append(match_expr)
                for sterm in short_terms:
                    if '"' in sterm or "\\" in sterm:
                        continue
                    escaped = sterm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    where_parts.append("i.payload LIKE ? ESCAPE '\\'")
                    params.append(f"%{escaped}%")

                cur = conn.execute(
                    f"""
                    SELECT i.session_id, i.item_id, i.item_type, i.payload, i.created_at
                    FROM items_fts
                    INNER JOIN items i ON i.seq = items_fts.rowid
                    INNER JOIN sessions s ON s.session_id = i.session_id
                    WHERE {' AND '.join(where_parts)}
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
                for sid, item_id, item_type, payload_str, created_at in cur:
                    try:
                        item_obj = json.loads(payload_str)
                    except Exception:
                        continue
                    text = self._extract_item_search_text(item_obj)
                    _record_hit(sid, item_id, item_type, text, created_at)
            else:
                like_clauses: List[str] = []
                params = [prof]
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
                for sid, item_id, item_type, payload_str, created_at in cur:
                    try:
                        item_obj = json.loads(payload_str)
                    except Exception:
                        continue
                    text = self._extract_item_search_text(item_obj)
                    _record_hit(sid, item_id, item_type, text, created_at)

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
