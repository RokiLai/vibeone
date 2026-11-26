"""Codex model catalog, mode/policy mapping, skills, and account cache mixin."""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ... import config
from ..base import SessionRuntime

logger = config.logger

CODEX_MODES: List[Dict[str, str]] = [
    {"id": "auto", "name": "auto (自动执行)"},
    {"id": "full-access", "name": "full-access (完全权限)"},
    {"id": "on-request", "name": "on-request (按需审批)"},
    {"id": "read-only", "name": "read-only (只读)"},
]

CODEX_BUILTIN_COMMANDS: List[Dict[str, Any]] = [
    {"name": "compact", "description": "压缩当前 Codex 对话历史，释放上下文窗口", "kind": "codex", "instant": True},
    {"name": "review", "description": "审查代码改动（默认未提交改动；支持 /review branch <分支> / commit <sha> / <要求>）", "kind": "codex", "instant": False},
    {"name": "fork", "description": "从当前 Codex 会话分叉出一个新会话继续探索", "kind": "codex", "instant": True},
]


class CodexCatalogMixin:
    """Account cache, model catalog expansion, mode/model setters, and skills discovery."""

    def _load_cached_codex_account_state(self, account_key: Optional[str] = None) -> None:
        key = account_key or self._codex_account_key or "default"
        raw = self.store.get_meta("codex_account_cache", {})
        if not isinstance(raw, dict):
            return
        entry = raw.get(key)
        if not isinstance(entry, dict):
            return
        models = entry.get("available_models")
        if isinstance(models, list) and models and not self.available_models:
            self.available_models = [dict(m) for m in models if isinstance(m, dict)]
        default_id = str(entry.get("default_model_id") or "").strip()
        if default_id and not self._default_model_id:
            self._default_model_id = default_id
        acct = entry.get("account")
        if isinstance(acct, dict) and acct and not self._codex_account:
            self._codex_account = dict(acct)

    def _save_cached_codex_account_state(self, account_key: Optional[str] = None) -> None:
        key = account_key or self._codex_account_key or "default"
        raw = self.store.get_meta("codex_account_cache", {})
        if not isinstance(raw, dict):
            raw = {}
        entry = dict(raw.get(key) or {})
        if self.available_models:
            entry["available_models"] = list(self.available_models)
        if self._default_model_id:
            entry["default_model_id"] = self._default_model_id
        if self._codex_account:
            entry["account"] = dict(self._codex_account)
        entry["updated_at"] = time.time()
        raw[key] = entry
        if len(raw) > 20:
            for old_key in list(raw.keys())[:-20]:
                raw.pop(old_key, None)
        self.store.set_meta("codex_account_cache", raw)

    @staticmethod
    def _extract_models_list_from_file(path: Path) -> Optional[List[Dict[str, Any]]]:
        try:
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("models"), list) and data["models"]:
                return [m for m in data["models"] if isinstance(m, dict)]
        except Exception:
            return None
        return None

    def _sync_codex_model_catalog_from_home(self, codex_home: Path, account_key: str) -> Optional[Path]:
        """Persist a per-account `model_catalog_json` snapshot from `models_cache.json` if available."""
        cache_dir = config.CODEX_MODEL_CACHE_DIR
        cache_path = cache_dir / f"catalog_{account_key}.json"
        home_cache = codex_home / "models_cache.json"

        should_copy = False
        if home_cache.is_file():
            if not cache_path.is_file():
                should_copy = True
            else:
                try:
                    should_copy = home_cache.stat().st_mtime > cache_path.stat().st_mtime
                except OSError:
                    should_copy = False

        if should_copy:
            models = self._extract_models_list_from_file(home_cache)
            if models:
                try:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    tmp_path = cache_dir / f".catalog_{account_key}.{os.getpid()}.tmp"
                    tmp_path.write_text(json.dumps({"models": models}, ensure_ascii=False), encoding="utf-8")
                    os.replace(str(tmp_path), str(cache_path))
                    logger.info(
                        "Updated per-account Codex model catalog cache: %s (%d models)",
                        cache_path.name,
                        len(models),
                    )
                except OSError as exc:
                    logger.warning("Failed to write Codex model catalog cache: %s", exc)

        if self._extract_models_list_from_file(cache_path):
            return cache_path
        return None

    async def _read_stderr_loop(self, proc: asyncio.subprocess.Process) -> None:
        assert proc and proc.stderr
        try:
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                self._recent_stderr.append(line)
                if len(self._recent_stderr) > 30:
                    self._recent_stderr = self._recent_stderr[-30:]
                if any(k in line.lower() for k in ("error", "failed", "fatal", "panic", "denied", "permitted")):
                    logger.warning("[codex stderr] %s", line)
                elif not self.connected:
                    logger.info("[codex stderr] %s", line)
                else:
                    logger.debug("[codex stderr] %s", line)
        except asyncio.CancelledError:
            pass

    def _get_preferred_mode(self) -> str:
        saved = str(self.store.get_meta("codex_preferred_mode") or "").strip()
        if saved in {m["id"] for m in CODEX_MODES}:
            return saved
        return "auto"

    def _current_profile_email(self) -> Optional[str]:
        email = self._codex_account.get("email")
        if email:
            return str(email)
        plan = self._codex_account.get("planType")
        if plan:
            return f"Codex ({plan})"
        if self._codex_account.get("type") == "apiKey":
            return "API Key"
        _, hint = config._read_codex_auth_hint()
        return hint or "Codex CLI"

    @staticmethod
    def _mode_to_codex_policy(mode_id: str) -> Tuple[str, str, Dict[str, Any]]:
        m = str(mode_id or "auto").strip()
        if m == "full-access":
            return "never", "danger-full-access", {"type": "dangerFullAccess"}
        if m == "on-request":
            return "on-request", "workspace-write", {"type": "workspaceWrite", "networkAccess": True}
        if m == "read-only":
            return "on-request", "read-only", {"type": "readOnly", "networkAccess": False}
        return "never", "workspace-write", {"type": "workspaceWrite", "networkAccess": True}

    @staticmethod
    def _split_model_and_effort(model_id: str) -> Tuple[Optional[str], Optional[str]]:
        raw = str(model_id or "").strip()
        if not raw:
            return None, None
        if "::" in raw:
            base, effort = raw.split("::", 1)
            return base.strip() or None, effort.strip() or None
        return raw, None

    @staticmethod
    def _build_codex_models(raw_models: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
        out: List[Dict[str, Any]] = []
        default_id = ""
        for m in raw_models:
            if not isinstance(m, dict) or m.get("hidden"):
                continue
            base_id = str(m.get("id") or m.get("model") or "").strip()
            if not base_id:
                continue
            disp = str(m.get("displayName") or base_id).strip()
            efforts = m.get("supportedReasoningEfforts") or []
            def_eff = str(m.get("defaultReasoningEffort") or "").strip()
            is_def = bool(m.get("isDefault"))

            if len(efforts) > 1:
                for e in efforts:
                    if not isinstance(e, dict):
                        continue
                    eff = str(e.get("reasoningEffort") or "").strip()
                    if not eff:
                        continue
                    mid = f"{base_id}::{eff}"
                    out.append(
                        {
                            "modelId": mid,
                            "id": mid,
                            "name": f"{disp} ({eff})",
                            "description": str(e.get("description") or m.get("description") or ""),
                        }
                    )
                    if is_def and eff == def_eff:
                        default_id = mid
            else:
                eff = (
                    str(efforts[0].get("reasoningEffort") or "").strip()
                    if len(efforts) == 1 and isinstance(efforts[0], dict)
                    else def_eff
                )
                mid = f"{base_id}::{eff}" if eff else base_id
                out.append(
                    {
                        "modelId": mid,
                        "id": mid,
                        "name": disp,
                        "description": str(m.get("description") or ""),
                    }
                )
                if is_def:
                    default_id = mid
        if not default_id and out:
            default_id = str(out[0]["modelId"])
        return out, default_id

    def _apply_codex_session_meta(self, res: Dict[str, Any], rt: Optional[SessionRuntime] = None) -> None:
        target = rt or self._active_runtime()
        if not target:
            return
        target.current_mode = self._get_preferred_mode()
        preferred_model = self._get_preferred_model()
        valid_ids = self._get_available_model_ids()
        if preferred_model and (not valid_ids or preferred_model in valid_ids):
            target.current_model = preferred_model
            return
        raw_model = str(res.get("model") or "").strip()
        raw_effort = str(res.get("reasoningEffort") or "").strip()
        cand = f"{raw_model}::{raw_effort}" if (raw_model and raw_effort) else raw_model
        if cand and (not valid_ids or cand in valid_ids):
            target.current_model = cand
        elif raw_model and (not valid_ids or raw_model in valid_ids):
            target.current_model = raw_model
        elif self._default_model_id:
            target.current_model = self._default_model_id

    def _sync_preferred_model(self, rt: Optional[SessionRuntime] = None) -> None:
        target = rt or self._active_runtime()
        if not target or target.is_busy:
            return
        preferred_model = self._get_preferred_model()
        valid_ids = self._get_available_model_ids()
        if preferred_model and (not valid_ids or preferred_model in valid_ids):
            target.current_model = preferred_model

    async def set_mode(self, mode_id: str, sid: Optional[str] = None) -> None:
        mid = str(mode_id or "").strip()
        if mid not in {m["id"] for m in CODEX_MODES}:
            raise ValueError(f"不支持的模式: {mid}")
        target_sid = sid or self.session_id
        rt = self._runtimes.get(target_sid) if target_sid else None
        if rt:
            rt.current_mode = mid
        else:
            self.current_mode = mid
        self.store.set_meta("codex_preferred_mode", mid)
        await self.broadcast_status()

    async def set_model(self, model_id: str, sid: Optional[str] = None) -> None:
        model_id = str(model_id or "").strip()
        if not model_id:
            raise ValueError("模型 ID 不能为空")
        await self.start()
        target_sid = sid or self.session_id
        if not target_sid:
            raise RuntimeError("Codex 会话未就绪")
        valid_ids = self._get_available_model_ids()
        if valid_ids and model_id not in valid_ids:
            raise ValueError(f"不支持的模型: {model_id}")
        rt = self._runtimes.get(target_sid)
        if rt:
            rt.current_model = model_id
        else:
            self.current_model = model_id
        self._set_preferred_model(model_id)
        await self.broadcast_status()

    async def _refresh_codex_skills(self) -> None:
        cmds: List[Dict[str, Any]] = [dict(c) for c in CODEX_BUILTIN_COMMANDS]
        seen: Set[str] = {str(c["name"]).lower() for c in cmds}
        skills_map: Dict[str, Dict[str, Any]] = {}
        try:
            params: Dict[str, Any] = {"cwds": [self.workdir]} if self.workdir else {}
            skills_res = await self._send_request("skills/list", params, timeout=10.0)
            for entry in skills_res.get("data") or []:
                for sk in (entry.get("skills") or []) if isinstance(entry, dict) else []:
                    if not isinstance(sk, dict) or not sk.get("enabled", True):
                        continue
                    name = str(sk.get("name") or "").strip()
                    if not name:
                        continue
                    low = name.lower()
                    path = str(sk.get("path") or "").strip()
                    desc = str(sk.get("shortDescription") or sk.get("description") or "").strip()
                    if path:
                        skills_map[low] = {"name": name, "path": path, "description": desc}
                    if low not in seen:
                        seen.add(low)
                        cmds.append(
                            {
                                "name": name,
                                "description": desc or f"调用 Codex 技能 {name}",
                                "kind": "skill",
                                "instant": False,
                            }
                        )
        except Exception as exc:
            logger.debug("Codex skills/list failed: %s", str(exc) or type(exc).__name__)
        self._codex_skills = skills_map
        self.available_commands = cmds
