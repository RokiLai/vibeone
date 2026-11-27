"""Lightweight frontend telemetry ingestion, rotation, query, and HTTP trace helpers."""

import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from .. import config

telemetry_logger = logging.getLogger("vibeone.telemetry")

_MAX_BODY_BYTES = 64 * 1024
_MAX_BATCH_EVENTS = 60
_MAX_DATA_JSON_CHARS = 2048
_MAX_LOG_BYTES = 5 * 1024 * 1024
_MAX_ROTATED_FILES = 3
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SEVERE_EVENTS = {
    "js_error",
    "unhandled_rejection",
    "jank_freeze",
    "ws_reconnect",
    "api_error",
    "slow_render",
}


class TelemetryEvent(BaseModel):
    ts: Optional[float] = None
    event: str
    cat: Optional[str] = "behavior"
    level: Optional[str] = "info"
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    profile: Optional[str] = None
    dur_ms: Optional[float] = None
    data: Optional[Dict[str, Any]] = None


class TelemetryBatchRequest(BaseModel):
    cid: Optional[str] = None
    page_id: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    events: List[TelemetryEvent]


def sanitize_trace_token(value: Optional[str]) -> str:
    raw = str(value or "").strip()
    if not raw or not _TRACE_ID_RE.match(raw):
        return ""
    return raw


def _default_log_path() -> Path:
    env_path = os.environ.get("VIBEONE_TELEMETRY_LOG_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    if "unittest" in sys.modules or "pytest" in sys.modules:
        return Path(tempfile.gettempdir()) / f"vibeone-test-telemetry-{os.getpid()}.jsonl"
    return config.BASE_DIR / "logs" / "frontend-telemetry.jsonl"


def _truncate_str(val: Any, max_len: int = 240) -> str:
    s = str(val or "")
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _sanitize_data(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    cleaned: Dict[str, Any] = {}
    for k, v in list(data.items())[:24]:
        key = _truncate_str(k, 48)
        if isinstance(v, (int, float, bool)) or v is None:
            cleaned[key] = v
        elif isinstance(v, str):
            cleaned[key] = _truncate_str(v, 300)
        else:
            try:
                cleaned[key] = _truncate_str(json.dumps(v, ensure_ascii=False), 300)
            except Exception:
                cleaned[key] = _truncate_str(v, 120)
    try:
        encoded = json.dumps(cleaned, ensure_ascii=False)
        if len(encoded) > _MAX_DATA_JSON_CHARS:
            return {"_truncated": True, "preview": encoded[: _MAX_DATA_JSON_CHARS - 32]}
    except Exception:
        return {}
    return cleaned


class TelemetryLogStore:
    """Thread-safe size-rotating JSONL store for frontend telemetry events."""

    def __init__(
        self,
        log_path: Optional[Path] = None,
        max_bytes: int = _MAX_LOG_BYTES,
        max_rotated: int = _MAX_ROTATED_FILES,
    ) -> None:
        self.log_path = Path(log_path) if log_path else _default_log_path()
        self.max_bytes = max(4096, int(max_bytes))
        self.max_rotated = max(1, int(max_rotated))
        self._lock = threading.Lock()

    def _rotate_if_needed(self) -> None:
        if not self.log_path.is_file():
            return
        try:
            if self.log_path.stat().st_size < self.max_bytes:
                return
        except OSError:
            return
        for idx in range(self.max_rotated, 0, -1):
            src = Path(f"{self.log_path}.{idx - 1}") if idx > 1 else self.log_path
            dst = Path(f"{self.log_path}.{idx}")
            if src.exists():
                try:
                    src.replace(dst)
                except OSError:
                    pass

    def append_records(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 0
        lines = [json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n" for rec in records]
        payload = "".join(lines)
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed()
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(payload)
        return len(records)

    def query_recent(
        self,
        *,
        limit: int = 100,
        trace_id: Optional[str] = None,
        cid: Optional[str] = None,
        session_id: Optional[str] = None,
        cat: Optional[str] = None,
        event: Optional[str] = None,
        level: Optional[str] = None,
        since_ts: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        max_items = max(1, min(int(limit or 100), 500))
        candidates = [self.log_path]
        rotated_one = Path(f"{self.log_path}.1")
        if rotated_one.is_file():
            candidates.append(rotated_one)

        matched: deque = deque(maxlen=max_items)
        with self._lock:
            files_to_read = [p for p in reversed(candidates) if p.is_file()]
            for path in files_to_read:
                try:
                    with path.open("r", encoding="utf-8", errors="replace") as fh:
                        for raw_line in fh:
                            raw_line = raw_line.strip()
                            if not raw_line:
                                continue
                            try:
                                rec = json.loads(raw_line)
                            except json.JSONDecodeError:
                                continue
                            if trace_id and rec.get("trace_id") != trace_id:
                                continue
                            if cid and rec.get("cid") != cid:
                                continue
                            if session_id and rec.get("session_id") != session_id:
                                continue
                            if cat and rec.get("cat") != cat:
                                continue
                            if event and rec.get("event") != event:
                                continue
                            if level and rec.get("level") != level:
                                continue
                            if since_ts is not None:
                                rec_ts = float(rec.get("ts") or rec.get("server_ts") or 0)
                                if rec_ts < since_ts:
                                    continue
                            matched.append(rec)
                except OSError:
                    continue
        return list(matched)


def log_http_trace(request: Request, status_code: int, duration_ms: float) -> Tuple[str, str]:
    trace_id = sanitize_trace_token(request.headers.get("x-vibeone-trace"))
    cid = sanitize_trace_token(request.headers.get("x-vibeone-client"))
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/telemetry"):
        if status_code >= 400 or duration_ms >= 500.0:
            lvl = logging.WARNING if status_code >= 400 or duration_ms >= 1500.0 else logging.INFO
            telemetry_logger.log(
                lvl,
                "http_trace trace=%s cid=%s method=%s path=%s status=%d dur_ms=%.1f",
                trace_id or "-",
                cid or "-",
                request.method,
                path,
                status_code,
                duration_ms,
            )
    return trace_id, cid


def build_telemetry_router(
    require_http_auth: Callable[..., Any],
    telemetry_store: Optional[TelemetryLogStore] = None,
) -> Tuple[APIRouter, Dict[str, Any]]:
    router = APIRouter()
    log_store = telemetry_store or TelemetryLogStore()

    @router.post("/api/telemetry")
    async def ingest_telemetry(request: Request, _=Depends(require_http_auth)):
        raw_body = await request.body()
        if len(raw_body) > _MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Telemetry payload too large")
        try:
            payload_dict = json.loads(raw_body.decode("utf-8"))
            batch = TelemetryBatchRequest(**payload_dict)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid telemetry payload")

        cid = sanitize_trace_token(batch.cid or request.headers.get("x-vibeone-client")) or "anon"
        page_id = sanitize_trace_token(batch.page_id) or ""
        meta = _sanitize_data(batch.meta)
        server_ts = round(time.time() * 1000)

        records: List[Dict[str, Any]] = []
        for ev in batch.events[:_MAX_BATCH_EVENTS]:
            ev_name = _truncate_str(ev.event, 64).strip()
            if not ev_name:
                continue
            trace_id = sanitize_trace_token(ev.trace_id)
            level = (ev.level or "info").lower()
            if level not in ("debug", "info", "warn", "error"):
                level = "info"
            rec: Dict[str, Any] = {
                "server_ts": server_ts,
                "ts": round(float(ev.ts), 1) if ev.ts is not None else server_ts,
                "cid": cid,
                "page_id": page_id,
                "event": ev_name,
                "cat": _truncate_str(ev.cat or "behavior", 32),
                "level": level,
                "trace_id": trace_id,
                "session_id": _truncate_str(ev.session_id, 96) if ev.session_id else "",
                "profile": _truncate_str(ev.profile, 48) if ev.profile else "",
            }
            if ev.dur_ms is not None:
                rec["dur_ms"] = round(float(ev.dur_ms), 2)
            cleaned_data = _sanitize_data(ev.data)
            if cleaned_data:
                rec["data"] = cleaned_data
            if meta and ev_name == "app_boot":
                rec["meta"] = meta
            records.append(rec)

            if level in ("warn", "error") or ev_name in _SEVERE_EVENTS:
                telemetry_logger.log(
                    logging.WARNING if level in ("warn", "error") else logging.INFO,
                    "fe_event event=%s cat=%s trace=%s cid=%s page=%s sid=%s dur_ms=%s data=%s",
                    ev_name,
                    rec["cat"],
                    trace_id or "-",
                    cid,
                    page_id or "-",
                    rec["session_id"] or "-",
                    rec.get("dur_ms", "-"),
                    json.dumps(cleaned_data, ensure_ascii=False)[:240] if cleaned_data else "{}",
                )

        accepted = log_store.append_records(records)
        return {"ok": True, "accepted": accepted}

    @router.get("/api/telemetry/recent")
    async def get_recent_telemetry(
        limit: int = Query(default=100, ge=1, le=500),
        trace_id: Optional[str] = None,
        cid: Optional[str] = None,
        session_id: Optional[str] = None,
        cat: Optional[str] = None,
        event: Optional[str] = None,
        level: Optional[str] = None,
        since_ts: Optional[float] = None,
        _=Depends(require_http_auth),
    ):
        items = log_store.query_recent(
            limit=limit,
            trace_id=trace_id,
            cid=cid,
            session_id=session_id,
            cat=cat,
            event=event,
            level=level,
            since_ts=since_ts,
        )
        return {"ok": True, "count": len(items), "items": items}

    return router, {
        "telemetry_store": log_store,
        "ingest_telemetry": ingest_telemetry,
        "get_recent_telemetry": get_recent_telemetry,
    }
