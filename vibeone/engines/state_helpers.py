"""Persisted usage, prompt queue, and workdir shortcut helpers for engine managers."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

from .. import config


class EngineStateHelpersMixin:
    """Usage/queue persistence and workdir canonicalization helpers."""

    def _get_saved_usage(self, sid: Optional[str]) -> Dict[str, Any]:
        if not sid:
            return {"used": 0, "size": 0}
        raw = self.store.get_meta("session_usage", {})
        if isinstance(raw, dict):
            entry = raw.get(sid)
            if isinstance(entry, dict) and int(entry.get("size") or 0) > 0:
                return {
                    "used": int(entry.get("used") or 0),
                    "size": int(entry.get("size") or 0),
                }
        return {"used": 0, "size": 0}

    def _save_usage(self, sid: Optional[str], usage: Dict[str, Any]) -> None:
        if not sid or not isinstance(usage, dict):
            return
        size = int(usage.get("size") or 0)
        used = int(usage.get("used") or 0)
        if size <= 0:
            return
        raw = self.store.get_meta("session_usage", {})
        if not isinstance(raw, dict):
            raw = {}
        raw[sid] = {"used": used, "size": size}
        if len(raw) > 200:
            for old_key in list(raw.keys())[:-200]:
                raw.pop(old_key, None)
        self.store.set_meta("session_usage", raw)

    def _delete_saved_usage(self, sid: Optional[str]) -> None:
        if not sid:
            return
        raw = self.store.get_meta("session_usage", {})
        if isinstance(raw, dict) and sid in raw:
            raw.pop(sid, None)
            self.store.set_meta("session_usage", raw)

    def _get_saved_queue(self, sid: Optional[str]) -> List[Dict[str, Any]]:
        if not sid:
            return []
        raw = self.store.get_meta("session_queues", {})
        if isinstance(raw, dict):
            entries = raw.get(sid)
            if isinstance(entries, list):
                return [e for e in entries if isinstance(e, dict) and e.get("id")]
        return []

    def _save_queue(self, sid: Optional[str], queue: List[Dict[str, Any]]) -> None:
        if not sid:
            return
        raw = self.store.get_meta("session_queues", {})
        if not isinstance(raw, dict):
            raw = {}
        if queue:
            raw[sid] = list(queue)
        else:
            raw.pop(sid, None)
        if len(raw) > 100:
            for old_key in list(raw.keys())[:-100]:
                raw.pop(old_key, None)
        self.store.set_meta("session_queues", raw)

    def _delete_saved_queue(self, sid: Optional[str]) -> None:
        if not sid:
            return
        raw = self.store.get_meta("session_queues", {})
        if isinstance(raw, dict) and sid in raw:
            raw.pop(sid, None)
            self.store.set_meta("session_queues", raw)

    @staticmethod
    def _canonical_workdir(path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        if raw.startswith("file://"):
            raw = unquote(urlparse(raw).path)
        expanded = os.path.abspath(os.path.expanduser(raw))
        try:
            return os.path.realpath(expanded)
        except OSError:
            return expanded

    @classmethod
    def _normalize_workdir(cls, path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            raise ValueError("目录路径不能为空")
        expanded = os.path.abspath(os.path.expanduser(raw))
        if not os.path.exists(expanded):
            raise ValueError(f"目录不存在: {expanded}")
        if not os.path.isdir(expanded):
            raise ValueError(f"路径不是目录: {expanded}")
        return cls._canonical_workdir(expanded)

    def _remember_workdir(self, path: str) -> None:
        try:
            norm = os.path.abspath(os.path.expanduser(path))
            if not os.path.isdir(norm):
                return
            recent = self.store.get_meta("recent_workdirs", [])
            if not isinstance(recent, list):
                recent = []
            seen_real = {os.path.realpath(norm)}
            updated = [norm]
            for item in recent:
                if not isinstance(item, str) or not os.path.isdir(item):
                    continue
                real = os.path.realpath(item)
                if real not in seen_real:
                    seen_real.add(real)
                    updated.append(os.path.abspath(item))
            self.store.set_meta("recent_workdirs", updated[:12])
        except Exception:
            pass

    def get_shortcut_dirs(self) -> List[Dict[str, str]]:
        candidates: List[Tuple[str, str]] = []
        candidates.append((Path(self.workdir).name or self.workdir, self.workdir))

        recent = self.store.get_meta("recent_workdirs", [])
        if isinstance(recent, list):
            for p in recent:
                if isinstance(p, str) and os.path.isdir(p):
                    candidates.append((Path(p).name or p, os.path.abspath(p)))

        default_parent = os.path.dirname(config.VIBEONE_WORKDIR)
        if default_parent and os.path.isdir(default_parent):
            candidates.append((Path(default_parent).name or default_parent, default_parent))

        home = os.path.expanduser("~")
        projects_dir = os.path.join(home, "Projects")
        if os.path.isdir(projects_dir):
            candidates.append(("Projects", os.path.abspath(projects_dir)))
        if os.path.isdir(home):
            candidates.append(("~ (Home)", os.path.abspath(home)))

        shortcuts: List[Dict[str, str]] = []
        seen_real: Set[str] = set()
        for label, p in candidates:
            if not os.path.isdir(p):
                continue
            real = os.path.realpath(p)
            if real in seen_real:
                continue
            seen_real.add(real)
            shortcuts.append({"label": label, "path": p})
        return shortcuts[:12]
