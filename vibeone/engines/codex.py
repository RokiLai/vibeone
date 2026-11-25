import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import WebSocket

from .. import config
from ..push import WebPushManager
from ..store import SessionStore
from .acp import AgyACPManager, SessionRuntime

logger = config.logger

# ---------------------------------------------------------------------------
# Codex App-Server Subprocess Manager (JSON-RPC 2.0 over stdio)
# ---------------------------------------------------------------------------

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


class CodexAppServerManager(AgyACPManager):
    """
    Hosts `codex app-server --stdio` via asyncio.create_subprocess_exec.
    Implements the same public interface & WS/SQLite item model as AgyACPManager
    so ProfileManagerPool and the frontend work seamlessly across both engines.
    """

    def __init__(
        self,
        workdir: str,
        store: SessionStore,
        push: Optional[WebPushManager] = None,
        profile: Optional[str] = None,
        clients: Optional[Set[WebSocket]] = None,
        client_visible: Optional[Dict[WebSocket, bool]] = None,
        client_views: Optional[Dict[WebSocket, Dict[str, Optional[str]]]] = None,
        is_active_fn: Optional[Any] = None,
    ):
        super().__init__(
            workdir,
            store,
            push=push,
            profile=profile or config.CODEX_PROFILE_NAME,
            clients=clients,
            client_visible=client_visible,
            client_views=client_views,
            is_active_fn=is_active_fn,
        )
        self.backend = "codex"
        self.available_modes = list(CODEX_MODES)
        self.available_commands = list(CODEX_BUILTIN_COMMANDS)
        self._fallback_mode = self._get_preferred_mode()
        self.agent_info = {"name": "codex", "title": "OpenAI Codex"}
        self._codex_account: Dict[str, Any] = {}
        self._codex_rate_limits: Optional[Dict[str, Any]] = None
        self._codex_skills: Dict[str, Dict[str, Any]] = {}
        self._thread_turn_ids: Dict[str, str] = {}
        self._turn_futures: Dict[str, asyncio.Future] = {}
        self._compact_sids: Set[str] = set()
        self._default_model_id: str = ""
        self._recent_stderr: List[str] = []
        self._thread_list_cache: Optional[List[Dict[str, Any]]] = None
        self._thread_list_cache_time: float = 0.0
        self._thread_list_in_flight: Optional[asyncio.Future] = None
        self._codex_account_key: str = config.get_codex_account_cache_key(
            config.resolve_codex_home(self.current_profile)
        )
        self._account_refresh_task: Optional[asyncio.Task] = None
        self._load_cached_codex_account_state()

    def _invalidate_thread_cache(self) -> None:
        self._thread_list_cache = None
        self._thread_list_cache_time = 0.0

    def _upsert_thread_cache(self, thread_obj: Dict[str, Any]) -> None:
        if self._thread_list_cache is None or not isinstance(thread_obj, dict):
            return
        tid = str(thread_obj.get("id") or "").strip()
        if not tid:
            return
        updated = [t for t in self._thread_list_cache if str(t.get("id") or "").strip() != tid]
        updated.insert(0, thread_obj)
        self._thread_list_cache = updated

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

    @staticmethod
    def _map_codex_status(status: Any) -> str:
        s = str(status or "").lower()
        if s in ("completed", "done"):
            return "completed"
        if s in ("failed", "declined", "error"):
            return "failed"
        return "in_progress"

    @staticmethod
    def _format_file_change_title(changes: List[Dict[str, Any]]) -> str:
        paths = [Path(str(c.get("path") or "")).name for c in changes if isinstance(c, dict) and c.get("path")]
        if not paths:
            return "修改文件"
        if len(paths) == 1:
            return f"编辑 {paths[0]}"
        return f"编辑 {paths[0]} 等 {len(paths)} 个文件"

    @staticmethod
    def _format_codex_tool_output(item: Dict[str, Any]) -> str:
        err = item.get("error")
        if isinstance(err, dict) and err.get("message"):
            return f"Error: {err['message']}"
        res = item.get("result")
        if isinstance(res, dict):
            content = res.get("content")
            if isinstance(content, list):
                texts = [str(c.get("text")) for c in content if isinstance(c, dict) and c.get("text")]
                if texts:
                    return "\n".join(texts)
            return json.dumps(res, ensure_ascii=False, indent=2)
        if item.get("contentItems"):
            citems = item["contentItems"]
            if isinstance(citems, list):
                texts = [str(c.get("text")) for c in citems if isinstance(c, dict) and c.get("text")]
                if texts:
                    return "\n".join(texts)
            return json.dumps(citems, ensure_ascii=False, indent=2)
        return ""

    @classmethod
    def _convert_codex_item_to_ui(cls, item: Dict[str, Any], ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        itype = item.get("type")
        iid = str(item.get("id") or uuid.uuid4().hex[:12])
        now = float(ts) if ts is not None else time.time()

        if itype == "userMessage":
            texts: List[str] = []
            for blk in item.get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    t = str(blk.get("text") or "").strip()
                    if t:
                        texts.append(t)
            return {
                "id": f"user_{iid}",
                "type": "user",
                "text": "\n".join(texts),
                "images": [],
                "ts": now,
            }

        if itype == "agentMessage":
            return {
                "id": f"asst_{iid}",
                "type": "assistant",
                "text": str(item.get("text") or ""),
                "ts": now,
            }

        if itype == "reasoning":
            summary = [str(x) for x in (item.get("summary") or []) if x]
            content = [str(x) for x in (item.get("content") or []) if x]
            text = "\n".join(summary or content)
            return {
                "id": f"thought_{iid}",
                "type": "thought",
                "text": text,
                "ts": now,
            }

        if itype == "plan":
            return {
                "id": f"thought_{iid}",
                "type": "thought",
                "text": str(item.get("text") or ""),
                "ts": now,
            }

        if itype == "commandExecution":
            cmd = str(item.get("command") or "Shell")
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "commandExecution",
                "title": cmd,
                "kind": "execute",
                "status": cls._map_codex_status(item.get("status")),
                "args": {"command": cmd, "cwd": str(item.get("cwd") or "")},
                "output": str(item.get("aggregatedOutput") or ""),
                "ts": now,
            }

        if itype == "fileChange":
            changes = item.get("changes") or []
            diffs = []
            for c in changes:
                if isinstance(c, dict) and c.get("diff"):
                    diffs.append(f"--- {c.get('path', '')}\n{c.get('diff', '')}")
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "fileChange",
                "title": cls._format_file_change_title(changes),
                "kind": "edit",
                "status": cls._map_codex_status(item.get("status")),
                "args": {
                    "changes": [
                        {"path": c.get("path"), "kind": c.get("kind")}
                        for c in changes
                        if isinstance(c, dict)
                    ]
                },
                "output": "\n\n".join(diffs),
                "ts": now,
            }

        if itype in ("mcpToolCall", "dynamicToolCall"):
            server_name = str(item.get("server") or "").strip()
            tool_name = str(item.get("tool") or itype).strip()
            title = f"{server_name}:{tool_name}" if server_name else tool_name
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": tool_name,
                "title": title,
                "kind": "other",
                "status": cls._map_codex_status(item.get("status")),
                "args": item.get("arguments") or {},
                "output": cls._format_codex_tool_output(item),
                "ts": now,
            }

        if itype == "webSearch":
            q = str(item.get("query") or "Web Search")
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "webSearch",
                "title": f"搜索: {q}",
                "kind": "search",
                "status": "completed",
                "args": {"query": q},
                "output": "",
                "ts": now,
            }

        if itype == "imageView":
            p = str(item.get("path") or "")
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "imageView",
                "title": f"查看图片 {Path(p).name if p else ''}".strip(),
                "kind": "read",
                "status": "completed",
                "args": {"path": p},
                "output": "",
                "ts": now,
            }

        if itype == "collabAgentToolCall":
            tname = str(item.get("tool") or "subagent")
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": tname,
                "title": tname,
                "kind": "other",
                "status": cls._map_codex_status(item.get("status")),
                "args": {"prompt": str(item.get("prompt") or "")},
                "output": "",
                "ts": now,
            }

        if itype == "contextCompaction":
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "contextCompaction",
                "title": "压缩上下文历史",
                "kind": "other",
                "status": "completed",
                "args": {},
                "output": "",
                "ts": now,
            }

        if itype == "enteredReviewMode":
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "review",
                "title": "开始代码审查",
                "kind": "other",
                "status": "in_progress",
                "args": {},
                "output": str(item.get("review") or ""),
                "ts": now,
            }

        if itype == "exitedReviewMode":
            review_text = str(item.get("review") or "").strip()
            if review_text:
                return {
                    "id": f"asst_{iid}",
                    "type": "assistant",
                    "text": review_text,
                    "ts": now,
                }
            return None

        return None

    @classmethod
    def _convert_codex_turns_to_items(cls, turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for turn in turns or []:
            if not isinstance(turn, dict):
                continue
            ts = float(turn.get("startedAt") or time.time())
            for raw_item in turn.get("items") or []:
                ui_item = cls._convert_codex_item_to_ui(raw_item, ts=ts)
                if ui_item and (ui_item["type"] != "assistant" or ui_item.get("text")):
                    if ui_item["type"] == "thought" and not ui_item.get("text"):
                        continue
                    out.append(ui_item)
        return out

    async def _spawn_and_initialize(self) -> None:
        if self.proc or self._stdout_task or self._stderr_task:
            await self._stop_unlocked()

        codex_bin = config.resolve_codex_binary()
        if not codex_bin:
            raise RuntimeError("未找到 `codex` 可执行文件，请先安装 Codex CLI")

        codex_home = config.ensure_codex_home(self.current_profile)
        self._codex_account_key = config.get_codex_account_cache_key(codex_home)
        self._load_cached_codex_account_state(self._codex_account_key)
        catalog_path = self._sync_codex_model_catalog_from_home(codex_home, self._codex_account_key)

        env = os.environ.copy()
        env["HOME"] = str(config.REAL_HOME)
        env["USER_REAL_HOME"] = str(config.REAL_HOME)
        env["CODEX_HOME"] = str(codex_home)

        cmd: List[str] = [codex_bin, "app-server"]
        if catalog_path is not None:
            cmd.extend(["-c", f"model_catalog_json={json.dumps(str(catalog_path))}"])
        cmd.append("--stdio")
        logger.info(
            "Starting Codex app-server: %s (cwd=%s, catalog_cache=%s)",
            " ".join(cmd),
            self.workdir,
            catalog_path.name if catalog_path else "none",
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workdir,
            env=env,
            limit=16 * 1024 * 1024,
        )
        self._recent_stderr.clear()
        self.proc = proc
        self._stdout_task = asyncio.create_task(self._read_stdout_loop(proc))
        self._stderr_task = asyncio.create_task(self._read_stderr_loop(proc))

        try:
            try:
                init_res = await self._send_request(
                    "initialize",
                    {
                        "clientInfo": {"name": "vibeone", "title": "VibeOne", "version": "1.0.0"},
                        "capabilities": {"experimentalApi": True},
                    },
                    timeout=20.0,
                )
            except asyncio.TimeoutError as exc:
                tail = " | ".join(self._recent_stderr[-5:])
                raise RuntimeError(f"Codex app-server initialize 超时 (20s){': ' + tail if tail else ''}") from exc

            await self._send_notification("initialized", {})
            self.agent_info = {
                "name": "codex",
                "title": "OpenAI Codex",
                "userAgent": init_res.get("userAgent", ""),
            }
            self.supports_image_prompt = True
            logger.info("Codex app-server initialized: %s", self.agent_info.get("userAgent", ""))

            async def _fetch_account() -> None:
                try:
                    acc_res = await self._send_request("account/read", {}, timeout=10.0)
                    acct = acc_res.get("account") or {}
                    if acct:
                        self._codex_account = acct
                        self._save_cached_codex_account_state(self._codex_account_key)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning("Codex account/read failed: %s", str(exc) or type(exc).__name__)

            async def _fetch_models() -> None:
                try:
                    models_res = await self._send_request("model/list", {"limit": 100}, timeout=10.0)
                    models, def_id = self._build_codex_models(models_res.get("data") or [])
                    if models:
                        self.available_models = models
                        self._default_model_id = def_id
                        self._save_cached_codex_account_state(self._codex_account_key)
                    if catalog_path is None:
                        self._sync_codex_model_catalog_from_home(codex_home, self._codex_account_key)
                except Exception as exc:
                    logger.warning("Codex model/list failed: %s", str(exc) or type(exc).__name__)

            async def _fetch_skills() -> None:
                await self._refresh_codex_skills()

            await asyncio.gather(_fetch_models(), _fetch_skills())
            # Refresh account metadata in background so cold start doesn't block ~3.4s on account/read
            if self._account_refresh_task and not self._account_refresh_task.done():
                self._account_refresh_task.cancel()
            self._account_refresh_task = asyncio.create_task(_fetch_account())

            saved_sid = self._get_profile_session_id()
            resumed = False
            if saved_sid:
                try:
                    load_res, replayed_items = await self._load_or_resume_session(saved_sid, self.workdir)
                    thread_obj = load_res.get("thread") or {}
                    loaded_sid = str(thread_obj.get("id") or saved_sid)
                    self.session_id = loaded_sid
                    rt = self._get_or_create_runtime(
                        loaded_sid,
                        workdir=self.workdir,
                        title=thread_obj.get("name") or self.store.get_meta("session_title", "Codex"),
                    )
                    rt.loaded_in_acp = True
                    self._apply_codex_session_meta(load_res, rt=rt)
                    self._apply_loaded_items(loaded_sid, replayed_items)
                    rt.usage = self._get_saved_usage(loaded_sid)
                    self.store.upsert_session(loaded_sid, self.workdir, rt.title, profile=self.current_profile)
                    resumed = True
                except Exception as exc:
                    logger.info("Could not resume Codex thread %s (%s); creating new thread", saved_sid, exc)

            if not resumed:
                await self._create_new_session(clear_history=not bool(self.items))

            self.connected = True
            self.last_error = None
            await self.broadcast_status()
        except BaseException:
            await self._stop_unlocked()
            raise

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

    async def _load_or_resume_session(
        self, sid: str, cwd: str, force_load: bool = False
    ) -> Tuple[Dict[str, Any], Optional[List[Dict[str, Any]]]]:
        has_local = self.store.count_items(sid) > 0
        should_load_turns = (not has_local) or force_load
        approval_policy, sandbox_mode, _ = self._mode_to_codex_policy(self._get_preferred_mode())
        params: Dict[str, Any] = {
            "threadId": sid,
            "cwd": cwd,
            "excludeTurns": not should_load_turns,
            "approvalPolicy": approval_policy,
            "sandbox": sandbox_mode,
        }
        base_model, _ = self._split_model_and_effort(
            str(self._get_preferred_model() or self._default_model_id or "")
        )
        if base_model:
            params["model"] = base_model
        res = await self._send_request("thread/resume", params, timeout=25.0)
        if not should_load_turns:
            return res, None
        turns = (res.get("thread") or {}).get("turns") or []
        return res, self._convert_codex_turns_to_items(turns)

    async def _create_new_session(
        self, clear_history: bool = True, workdir: Optional[str] = None
    ) -> None:
        if workdir is not None:
            self.workdir = self._normalize_workdir(workdir)
        self._set_profile_workdir(self.workdir)
        self._unhide_workdir(self.workdir)
        self._remember_workdir(self.workdir)

        pref_mode = self._get_preferred_mode()
        approval_policy, sandbox_mode, _ = self._mode_to_codex_policy(pref_mode)
        pref_model = str(self._get_preferred_model() or self._default_model_id or "")
        base_model, _ = self._split_model_and_effort(pref_model)
        params: Dict[str, Any] = {
            "cwd": self.workdir,
            "approvalPolicy": approval_policy,
            "sandbox": sandbox_mode,
        }
        if base_model:
            params["model"] = base_model

        new_res = await self._send_request("thread/start", params, timeout=30.0)
        thread_obj = new_res.get("thread") or {}
        new_sid = str(thread_obj.get("id") or "").strip()
        if not new_sid:
            raise RuntimeError("Codex thread/start 未返回 thread.id")

        rt = SessionRuntime(new_sid, self.workdir, str(thread_obj.get("name") or "新会话"))
        rt.loaded_in_acp = True
        self._runtimes[new_sid] = rt
        self.session_id = new_sid
        self._apply_codex_session_meta(new_res, rt=rt)
        self._set_profile_session_id(new_sid)

        if clear_history:
            rt.items = []
            rt.items_by_id = {}
            rt.title = "新会话"
            self.store.set_meta("session_title", rt.title)
            rt.usage = {"used": 0, "size": 0}
            self.store.upsert_session(
                new_sid, self.workdir, rt.title, updated_at=0.0, touch=False, profile=self.current_profile
            )
            self._mark_session_empty(new_sid)
        else:
            self.store.upsert_session(new_sid, self.workdir, rt.title, touch=False, profile=self.current_profile)
        self._upsert_thread_cache(
            {
                "id": new_sid,
                "cwd": self.workdir,
                "name": rt.title,
                "createdAt": thread_obj.get("createdAt") or 0,
                "updatedAt": thread_obj.get("updatedAt") or 0,
            }
        )
        logger.info(
            "Created new Codex thread: %s (cwd=%s, mode=%s, model=%s)",
            new_sid,
            self.workdir,
            rt.current_mode,
            rt.current_model,
        )

    async def _archive_thread_bg(self, sid: str) -> None:
        if not self.connected or not sid:
            return
        try:
            await self._send_request("thread/archive", {"threadId": sid}, timeout=2.5)
        except Exception:
            pass

    async def _reclaim_empty_session_unlocked(self, sid: str) -> bool:
        if not sid:
            return False
        rt = self._runtimes.get(sid)
        if rt and rt.is_busy:
            return False
        if self.store.count_items(sid) > 0:
            self._unmark_session_empty(sid)
            return False
        self._unmark_session_empty(sid)
        self._delete_saved_usage(sid)
        self._delete_saved_queue(sid)
        self._runtimes.pop(sid, None)
        self.store.delete_session(sid)
        self._add_hidden_sessions({sid})
        if self._thread_list_cache is not None:
            self._thread_list_cache = [
                t for t in self._thread_list_cache if str(t.get("id") or "").strip() != sid
            ]
        if self.connected:
            asyncio.create_task(self._archive_thread_bg(sid))
        return True

    async def _fetch_cached_threads(self, max_age: float = 15.0) -> List[Dict[str, Any]]:
        now = time.time()
        if self._thread_list_cache is not None and (now - self._thread_list_cache_time) < max_age:
            return self._thread_list_cache

        if self._thread_list_in_flight is not None and not self._thread_list_in_flight.done():
            try:
                return await asyncio.shield(self._thread_list_in_flight)
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._thread_list_in_flight = fut
        try:
            res = await self._send_request("thread/list", {"archived": False, "limit": 100}, timeout=15.0)
            threads = [t for t in (res.get("data") or []) if isinstance(t, dict)]
            self._thread_list_cache = threads
            self._thread_list_cache_time = time.time()
            if not fut.done():
                fut.set_result(threads)
            return threads
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._thread_list_in_flight = None

    async def list_sessions(
        self,
        include_hidden: bool = False,
        active_session_id: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        hidden = set() if include_hidden else self._get_hidden_sessions()
        hidden_dirs = [] if include_hidden else self._get_hidden_workdirs()
        archived_map = self._get_archived_sessions()
        cur_sid = str(active_session_id or "").strip() or self.session_id
        viewed_sids = self._viewed_session_ids()
        if cur_sid:
            viewed_sids.add(cur_sid)
        merged: Dict[str, Dict[str, Any]] = {}
        inode_to_cwd: Dict[Tuple[int, int], str] = {}

        def resolve_group_cwd(raw_cwd: str) -> str:
            canon = self._canonical_workdir(raw_cwd) or raw_cwd
            if canon and os.path.isdir(canon):
                try:
                    st = os.stat(canon)
                    key = (st.st_dev, st.st_ino)
                    existing_cwd = inode_to_cwd.get(key)
                    if existing_cwd:
                        return existing_cwd
                    inode_to_cwd[key] = canon
                except OSError:
                    pass
            return canon

        if self.workdir:
            resolve_group_cwd(self.workdir)

        empty_tracked = self._get_empty_sessions()
        reclaimed_sids: Set[str] = set()
        for row in self.store.list_sessions(limit=200, profile=self.current_profile):
            sid = row["session_id"]
            cwd = resolve_group_cwd(row["workdir"])
            item_count = int(row["item_count"] or 0)
            rt = self._runtimes.get(sid)
            if (
                sid not in viewed_sids
                and sid not in archived_map
                and not (rt and rt.is_busy)
                and (item_count == 0 or sid in empty_tracked)
            ):
                if item_count == 0:
                    self.store.delete_session(sid)
                    self._runtimes.pop(sid, None)
                    reclaimed_sids.add(sid)
                    continue
                self._unmark_session_empty(sid)
            if not include_archived and sid in archived_map:
                continue
            if sid not in viewed_sids and (sid in hidden or self._is_workdir_hidden(cwd, hidden_dirs)):
                continue
            merged[sid] = {
                "session_id": sid,
                "cwd": cwd,
                "dir_name": Path(cwd).name or cwd,
                "title": row["title"] or "新会话",
                "updated_at": float(row["updated_at"] or 0),
                "has_local_history": item_count > 0,
                "is_current": sid == cur_sid,
            }

        sessions_with_items = self.store.get_sessions_with_items()
        stale_empty = {
            s
            for s in empty_tracked
            if s not in viewed_sids and s not in sessions_with_items and s not in archived_map
        }
        reclaimed_sids.update(stale_empty)
        if reclaimed_sids:
            self._add_hidden_sessions(reclaimed_sids)
            hidden.update(reclaimed_sids)
            for s in reclaimed_sids:
                self._unmark_session_empty(s)

        try:
            await self.start()
            threads = await self._fetch_cached_threads(max_age=15.0)
            for t in threads:
                if not isinstance(t, dict):
                    continue
                sid = str(t.get("id") or "").strip()
                if not sid or sid in self._btw_sessions:
                    continue
                raw_cwd = str(t.get("cwd") or "").strip() or self.workdir
                cwd = resolve_group_cwd(raw_cwd)
                if not include_archived and sid in archived_map:
                    merged.pop(sid, None)
                    continue
                if sid not in viewed_sids and (sid in hidden or self._is_workdir_hidden(cwd, hidden_dirs)):
                    merged.pop(sid, None)
                    continue
                title = str(t.get("name") or t.get("preview") or "").strip()
                if title:
                    title = " ".join(title.split())[:60]
                else:
                    title = "未命名会话"
                updated_ts = float(t.get("updatedAt") or t.get("createdAt") or 0)
                existing = merged.get(sid)
                if existing:
                    if title and existing["title"] in ("新会话", "Agy Remote", "VibeOne", "Codex", ""):
                        existing["title"] = title
                    if cwd and os.path.isdir(cwd):
                        existing["cwd"] = cwd
                        existing["dir_name"] = Path(cwd).name or cwd
                    if existing["has_local_history"]:
                        existing["updated_at"] = max(existing["updated_at"], updated_ts)
                    elif sid not in empty_tracked and updated_ts > 0 and existing["updated_at"] <= 0:
                        existing["updated_at"] = updated_ts
                else:
                    merged[sid] = {
                        "session_id": sid,
                        "cwd": cwd,
                        "dir_name": Path(cwd).name or cwd,
                        "title": title,
                        "updated_at": updated_ts,
                        "has_local_history": sid in sessions_with_items,
                        "is_current": sid == cur_sid,
                    }
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Codex thread/list failed: %s", str(exc) or type(exc).__name__)

        items = list(merged.values())
        for it in items:
            sid = it["session_id"]
            rt = self._runtimes.get(sid)
            it["is_current"] = sid == cur_sid
            it["is_busy"] = bool(rt and rt.is_busy)
            it["pending_permissions"] = len(rt.pending_permissions) if rt else 0
            it["queued_count"] = len(rt.queued_prompts) if rt else 0
            it["is_archived"] = sid in archived_map
            it["archived_at"] = float(archived_map.get(sid, 0.0))
        items.sort(
            key=lambda x: (
                not (x["session_id"] in empty_tracked and (x["updated_at"] or 0) <= 0),
                -(x["updated_at"] or 0),
            )
        )
        return items

    async def _close_background_session(self, sid: str) -> None:
        self._invalidate_thread_cache()
        if self.connected:
            try:
                await self._send_request("thread/unsubscribe", {"threadId": sid}, timeout=3.0)
            except Exception:
                pass

    async def remove_session(self, session_id: str) -> Dict[str, Any]:
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id 不能为空")
        async with self._session_lock:
            was_current = sid == self.session_id
            rt = self._runtimes.get(sid)
            if rt and rt.is_busy:
                await self.cancel_current_turn(sid=sid)
            self._runtimes.pop(sid, None)
            self._unmark_session_empty(sid)
            self._delete_saved_usage(sid)
            self._delete_saved_queue(sid)
            self._unarchive_sessions({sid})
            self._add_hidden_sessions({sid})
            self.store.delete_session(sid)
            self._invalidate_thread_cache()

            if not was_current and self.connected:
                try:
                    await self._send_request("thread/unsubscribe", {"threadId": sid}, timeout=3.0)
                except Exception:
                    pass

            affected_clients = self._clients_for_session(sid) if self._client_views is not None else []
            if was_current or affected_clients:
                await self.start()
                if was_current:
                    self.session_id = None
                    remaining = [s for s in await self.list_sessions() if s["session_id"] != sid]
                    switched = False
                    for cand in remaining:
                        cand_cwd = cand["cwd"] if cand.get("cwd") and os.path.isdir(cand["cwd"]) else self.workdir
                        try:
                            await self._activate_existing_session_unlocked(
                                cand["session_id"],
                                cand_cwd,
                                cand.get("title"),
                                target_updated_at=cand.get("updated_at"),
                            )
                            switched = True
                            break
                        except Exception as exc:
                            logger.warning("Fallback switch to %s failed: %s", cand["session_id"], exc)
                    if not switched:
                        await self._create_new_session(clear_history=True)
                if self._client_views is not None:
                    for cws in affected_clients:
                        self._client_views[cws] = {"profile": self.current_profile, "session_id": self.session_id}
                    if affected_clients:
                        await self.broadcast(
                            {"type": "snapshot", "data": self.get_snapshot(sid=self.session_id)},
                            targets=affected_clients,
                        )
                elif was_current:
                    await self.broadcast({"type": "snapshot", "data": self.get_snapshot()})

            await self.broadcast({"type": "sessions_changed"})
            return {
                "ok": True,
                "removed_session_id": sid,
                "was_current": was_current,
                "current_session_id": self.session_id,
                "current_workdir": self.workdir,
            }

    async def switch_session(
        self,
        session_id: str,
        workdir: Optional[str] = None,
        title: Optional[str] = None,
        *,
        broadcast_all: bool = True,
        current_sid: Optional[str] = None,
        exclude_ws: Optional[WebSocket] = None,
    ) -> str:
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id 不能为空")
        async with self._session_lock:
            await self.start()
            prev_sid = current_sid or self.session_id
            prev_empty = bool(
                prev_sid
                and prev_sid != sid
                and self._is_session_empty(prev_sid)
                and not self._is_session_viewed_by_other(prev_sid, exclude_ws=exclude_ws)
            )
            existing_rt = self._runtimes.get(sid)
            if existing_rt and existing_rt.loaded_in_acp:
                target_cwd = self._normalize_workdir(workdir) if workdir else existing_rt.workdir
                existing_rt.workdir = target_cwd
                if title and existing_rt.title in ("会话", "新会话", "Agy Remote", "VibeOne", "Codex", ""):
                    existing_rt.title = title
                self.session_id = sid
                self.workdir = target_cwd
                self._sync_preferred_model(rt=existing_rt)
                if prev_empty and prev_sid and prev_sid != sid:
                    await self._reclaim_empty_session_unlocked(prev_sid)
                self._set_profile_session_id(sid)
                self._set_profile_workdir(self.workdir)
                self.store.set_meta("session_title", existing_rt.title)
                self._remember_workdir(self.workdir)
                if broadcast_all:
                    await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=sid)})
                if prev_empty or broadcast_all:
                    await self.broadcast({"type": "sessions_changed"})
                return sid

            target_cwd = None
            target_title = title
            target_updated_at: Optional[float] = None
            local = self.store.get_session(sid)
            if local:
                if float(local.get("updated_at") or 0) > 0:
                    target_updated_at = float(local["updated_at"])
                if not workdir and os.path.isdir(local["workdir"]):
                    target_cwd = self._canonical_workdir(local["workdir"])
                if not target_title:
                    target_title = local["title"]
            if workdir:
                target_cwd = self._normalize_workdir(workdir)
            if not target_cwd or target_updated_at is None:
                for s in await self.list_sessions(include_hidden=True):
                    if s["session_id"] == sid:
                        if not target_cwd and s["cwd"] and os.path.isdir(s["cwd"]):
                            target_cwd = self._canonical_workdir(s["cwd"])
                        if not target_title:
                            target_title = s["title"]
                        if target_updated_at is None and float(s.get("updated_at") or 0) > 0:
                            target_updated_at = float(s["updated_at"])
                        break
            if not target_cwd:
                target_cwd = self.workdir

            self._unhide_session(sid)
            self._unhide_workdir(target_cwd)
            load_res, replayed_items = await self._load_or_resume_session(sid, target_cwd)
            loaded_sid = str((load_res.get("thread") or {}).get("id") or sid)
            rt = self._get_or_create_runtime(loaded_sid, workdir=target_cwd, title=target_title)
            rt.loaded_in_acp = True
            self.session_id = loaded_sid
            self.workdir = target_cwd
            self._apply_codex_session_meta(load_res, rt=rt)
            self._apply_loaded_items(loaded_sid, replayed_items)
            if prev_empty and prev_sid and prev_sid != loaded_sid:
                await self._reclaim_empty_session_unlocked(prev_sid)
            stored_sess = self.store.get_session(loaded_sid)
            rt.title = target_title or (stored_sess["title"] if stored_sess else None) or rt.title or "会话"
            rt.usage = self._get_saved_usage(loaded_sid)
            self._set_profile_session_id(loaded_sid)
            self._set_profile_workdir(self.workdir)
            self.store.set_meta("session_title", rt.title)
            self.store.upsert_session(
                loaded_sid, self.workdir, rt.title, updated_at=target_updated_at, touch=False, profile=self.current_profile
            )
            self._remember_workdir(self.workdir)
            if broadcast_all:
                await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=loaded_sid)})
            if prev_empty or broadcast_all:
                await self.broadcast({"type": "sessions_changed"})
            return loaded_sid

    async def _activate_existing_session_unlocked(
        self,
        sid: str,
        target_cwd: str,
        target_title: Optional[str] = None,
        target_updated_at: Optional[float] = None,
    ) -> None:
        target_cwd = self._canonical_workdir(target_cwd) or target_cwd
        self._unhide_session(sid)
        self._unhide_workdir(target_cwd)
        existing_rt = self._runtimes.get(sid)
        if existing_rt and existing_rt.loaded_in_acp:
            rt = existing_rt
            rt.workdir = target_cwd
            self.session_id = sid
            self.workdir = target_cwd
            self._sync_preferred_model(rt=rt)
        else:
            load_res, replayed_items = await self._load_or_resume_session(sid, target_cwd)
            loaded_sid = str((load_res.get("thread") or {}).get("id") or sid)
            rt = self._get_or_create_runtime(loaded_sid, workdir=target_cwd, title=target_title)
            rt.loaded_in_acp = True
            self.session_id = loaded_sid
            self.workdir = target_cwd
            self._apply_codex_session_meta(load_res, rt=rt)
            self._apply_loaded_items(loaded_sid, replayed_items)
            rt.usage = self._get_saved_usage(loaded_sid)
        stored_sess = self.store.get_session(rt.session_id)
        rt.title = target_title or rt.title or (stored_sess["title"] if stored_sess else None) or "会话"
        self._set_profile_session_id(rt.session_id)
        self._set_profile_workdir(self.workdir)
        self.store.set_meta("session_title", rt.title)
        self.store.upsert_session(
            rt.session_id, self.workdir, rt.title, updated_at=target_updated_at, touch=False, profile=self.current_profile
        )
        self._remember_workdir(self.workdir)

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

    @staticmethod
    def _parse_review_target(arg: str) -> Dict[str, Any]:
        clean = str(arg or "").strip()
        if not clean:
            return {"type": "uncommittedChanges"}
        parts = clean.split(None, 1)
        head = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        if head in ("branch", "base") and rest:
            return {"type": "baseBranch", "branch": rest}
        if head == "commit" and rest:
            return {"type": "commit", "sha": rest}
        return {"type": "custom", "instructions": clean}

    async def _fork_current_session(
        self, sid: str, *, origin_ws: Optional[WebSocket] = None
    ) -> Dict[str, Any]:
        async with self._session_lock:
            rt = self._get_or_create_runtime(sid)
            approval_policy, sandbox_mode, _ = self._mode_to_codex_policy(rt.current_mode)
            base_model, _ = self._split_model_and_effort(rt.current_model)
            params: Dict[str, Any] = {
                "threadId": sid,
                "cwd": rt.workdir,
                "approvalPolicy": approval_policy,
                "sandbox": sandbox_mode,
            }
            if base_model:
                params["model"] = base_model
            res = await self._send_request("thread/fork", params, timeout=25.0)
            thread_obj = res.get("thread") or {}
            new_sid = str(thread_obj.get("id") or "").strip()
            if not new_sid:
                raise RuntimeError("Codex thread/fork 未返回新会话 ID")

            base_title = rt.title if rt.title and rt.title not in ("新会话", "VibeOne", "Codex") else "会话"
            fork_title = str(thread_obj.get("name") or f"{base_title} (分支)").strip()
            new_rt = SessionRuntime(new_sid, rt.workdir, fork_title)
            new_rt.loaded_in_acp = True
            new_rt.current_mode = rt.current_mode
            new_rt.current_model = rt.current_model
            new_rt.usage = dict(rt.usage)
            copied_items = [dict(it) for it in rt.items]
            new_rt.items = copied_items
            new_rt.items_by_id = {it["id"]: it for it in copied_items}

            self._runtimes[new_sid] = new_rt
            self.session_id = new_sid
            self.workdir = rt.workdir
            self._set_profile_session_id(new_sid)
            self._set_profile_workdir(self.workdir)
            self.store.set_meta("session_title", fork_title)
            self.store.upsert_session(new_sid, self.workdir, fork_title, touch=True, profile=self.current_profile)
            self._invalidate_thread_cache()
            if copied_items:
                self.store.replace_session_items(new_sid, copied_items)
            self._save_usage(new_sid, new_rt.usage)
            if origin_ws is not None and self._client_views is not None:
                self._client_views[origin_ws] = {"profile": self.current_profile, "session_id": new_sid}
                await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=new_sid)}, targets=[origin_ws])
                await self.broadcast(
                    {
                        "type": "toast",
                        "data": {"level": "info", "message": f"已分叉出新会话：{fork_title}"},
                    },
                    targets=[origin_ws],
                )
            else:
                await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=new_sid)})
                await self.broadcast(
                    {
                        "type": "toast",
                        "data": {"level": "info", "message": f"已分叉出新会话：{fork_title}"},
                    }
                )
            await self.broadcast({"type": "sessions_changed"})
            logger.info("Forked Codex thread %s -> %s", sid[:8], new_sid[:8])
            return {"queued": False, "forked_session_id": new_sid}

    async def send_prompt(
        self,
        text: str,
        display_text: Optional[str] = None,
        images: Optional[List[Dict[str, Any]]] = None,
        sid: Optional[str] = None,
        origin_ws: Optional[WebSocket] = None,
    ) -> Dict[str, Any]:
        clean = (text or "").strip()
        if clean.lower() == "/fork" or clean.lower().startswith("/fork "):
            await self.start()
            target_sid = str(sid or "").strip() or self.session_id
            if not target_sid:
                raise RuntimeError("Codex 会话未就绪")
            return await self._fork_current_session(target_sid, origin_ws=origin_ws)
        return await super().send_prompt(text, display_text=display_text, images=images, sid=sid)

    async def _build_codex_input(
        self, prompt_text: str, images: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for im in images or []:
            path = self._resolve_upload_file(im)
            if path:
                blocks.append({"type": "localImage", "path": str(path)})
        final_text = prompt_text.strip()
        if final_text.startswith("/"):
            parts = final_text[1:].split(None, 1)
            cmd_name = parts[0].strip().lower() if parts else ""
            rest = parts[1].strip() if len(parts) > 1 else ""
            skill = self._codex_skills.get(cmd_name)
            if skill and skill.get("path"):
                blocks.append({"type": "skill", "name": skill["name"], "path": skill["path"]})
                final_text = rest or f"请使用 {skill['name']} 技能处理当前任务。"
        if not final_text and blocks:
            final_text = "请查看以上图片并回复。"
        if final_text:
            blocks.append({"type": "text", "text": final_text})
        return blocks

    async def steer_queued_prompt(self, queue_id: str, sid: Optional[str] = None) -> bool:
        target_sid = sid or self.session_id
        if not target_sid or not queue_id:
            return False
        rt = self._runtimes.get(target_sid)
        if not rt:
            return False
        turn_id = self._thread_turn_ids.get(target_sid)
        if rt.is_busy and turn_id and self.connected:
            idx = next((i for i, q in enumerate(rt.queued_prompts) if q.get("id") == queue_id), -1)
            if idx < 0:
                return False
            entry = rt.queued_prompts.pop(idx)
            self._save_queue(target_sid, rt.queued_prompts)
            await self._broadcast_queue(target_sid)
            await self.broadcast({"type": "sessions_changed"})
            try:
                clean_text = entry.get("text", "")
                display_text = entry.get("display_text")
                images = entry.get("images") or []
                input_blocks = await self._build_codex_input(clean_text, images)
                await self._send_request(
                    "turn/steer",
                    {"threadId": target_sid, "expectedTurnId": turn_id, "input": input_blocks},
                    timeout=10.0,
                )
                user_item = {
                    "id": f"user_{uuid.uuid4().hex[:12]}",
                    "type": "user",
                    "text": display_text if display_text is not None else clean_text,
                    "images": list(images),
                    "ts": time.time(),
                }
                self._add_or_update_item(user_item, sid=target_sid)
                await self.broadcast({"type": "item_upsert", "data": user_item}, sid=target_sid)
                return True
            except Exception as exc:
                logger.warning("Codex turn/steer failed (%s), falling back to interrupt+start", exc)
                rt.queued_prompts.insert(0, entry)
        return await super().steer_queued_prompt(queue_id, sid=target_sid)

    async def _run_prompt_turn(
        self, sid: str, prompt_text: str, images: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        rt = self._get_or_create_runtime(sid)
        start_item_count = len(rt.items)
        failed = False
        abnormal_stop = False
        stop_reason = "end_turn"
        early_stop_msg = ""
        was_slash_cmd = prompt_text.lstrip().startswith("/")
        try:
            clean_prompt = prompt_text.strip()
            low_prompt = clean_prompt.lower()
            is_compact = low_prompt == "/compact" or low_prompt.startswith("/compact ")
            is_review = low_prompt == "/review" or low_prompt.startswith("/review ")

            loop = asyncio.get_running_loop()
            turn_fut: asyncio.Future = loop.create_future()
            self._turn_futures[sid] = turn_fut

            if is_compact:
                self._compact_sids.add(sid)
                await self._send_request("thread/compact/start", {"threadId": sid}, timeout=30.0)
                try:
                    completed_turn = await asyncio.wait_for(turn_fut, timeout=90.0)
                except asyncio.TimeoutError:
                    completed_turn = {"status": "completed"}
                if not any(
                    it.get("type") == "assistant" and str(it.get("text") or "").strip()
                    for it in rt.items[start_item_count:]
                ):
                    done_item = {
                        "id": f"asst_{uuid.uuid4().hex[:12]}",
                        "type": "assistant",
                        "text": "已完成当前 Codex 会话的上下文压缩。",
                        "ts": time.time(),
                    }
                    self._add_or_update_item(done_item, sid=sid)
                    await self.broadcast({"type": "item_upsert", "data": done_item}, sid=sid)
            elif is_review:
                review_arg = clean_prompt[len("/review"):].strip()
                target = self._parse_review_target(review_arg)
                start_res = await self._send_request(
                    "review/start",
                    {"threadId": sid, "delivery": "inline", "target": target},
                    timeout=30.0,
                )
                turn_obj = start_res.get("turn") or {}
                turn_id = str(turn_obj.get("id") or "")
                if turn_id:
                    self._thread_turn_ids[sid] = turn_id
                completed_turn = await turn_fut
            else:
                input_blocks = await self._build_codex_input(prompt_text, images)
                approval_policy, _, sandbox_policy = self._mode_to_codex_policy(rt.current_mode)
                base_model, effort = self._split_model_and_effort(rt.current_model)
                params: Dict[str, Any] = {
                    "threadId": sid,
                    "input": input_blocks,
                    "cwd": rt.workdir,
                    "approvalPolicy": approval_policy,
                    "sandboxPolicy": sandbox_policy,
                }
                if base_model:
                    params["model"] = base_model
                if effort:
                    params["effort"] = effort

                start_res = await self._send_request("turn/start", params, timeout=30.0)
                turn_obj = start_res.get("turn") or {}
                turn_id = str(turn_obj.get("id") or "")
                if turn_id:
                    self._thread_turn_ids[sid] = turn_id

                completed_turn = await turn_fut
            t_status = str((completed_turn or {}).get("status") or "completed").lower()
            if t_status == "interrupted":
                stop_reason = "cancelled"
            elif t_status == "failed":
                err_obj = (completed_turn or {}).get("error") or {}
                raise RuntimeError(str(err_obj.get("message") or "Codex 回合执行失败"))
            else:
                stop_reason = "end_turn"

            turn_new_items = rt.items[start_item_count:]
            has_assistant_reply = any(
                it.get("type") == "assistant" and str(it.get("text") or "").strip()
                for it in turn_new_items
            )
            if stop_reason == "end_turn" and not rt.cancel_requested and not has_assistant_reply and not was_slash_cmd:
                abnormal_stop = True
                early_stop_msg = "模型未生成最终回复，可点击「继续」接续执行。"
                notice_item = {
                    "id": f"warn_{uuid.uuid4().hex[:12]}",
                    "type": "error",
                    "kind": "early_stop",
                    "stop_reason": stop_reason,
                    "text": early_stop_msg,
                    "ts": time.time(),
                }
                self._add_or_update_item(notice_item, sid=sid)
                await self.broadcast({"type": "item_upsert", "data": notice_item}, sid=sid)

            await self.broadcast(
                {
                    "type": "turn_end",
                    "data": {
                        "stopReason": stop_reason,
                        "usage": rt.usage,
                        "early_stop_message": early_stop_msg or None,
                    },
                },
                sid=sid,
            )
            logger.info("Codex prompt turn finished (sid=%s, stopReason=%s)", sid[:8], stop_reason)
        except Exception as exc:
            failed = True
            logger.error("Codex prompt turn failed (sid=%s): %s", sid[:8], exc)
            err_item = {
                "id": f"err_{uuid.uuid4().hex[:12]}",
                "type": "error",
                "text": str(exc),
                "ts": time.time(),
            }
            self._add_or_update_item(err_item, sid=sid)
            await self.broadcast({"type": "item_upsert", "data": err_item}, sid=sid)
        finally:
            self._compact_sids.discard(sid)
            self._turn_futures.pop(sid, None)
            self._thread_turn_ids.pop(sid, None)
            if self.store.count_items(sid) > 0:
                self.store.touch_session(sid)
            was_cancelled = rt.cancel_requested or stop_reason.lower() == "cancelled"
            steer_entry = rt.steer_next_prompt
            rt.steer_next_prompt = None
            rt.cancel_requested = False
            rt.is_busy = False
            rt.active_assistant_id = None
            rt.active_thought_id = None
            rt.prompt_task = None

            next_queued = None
            if steer_entry is not None and self.connected:
                next_queued = steer_entry
            elif not failed and not was_cancelled and not abnormal_stop and rt.queued_prompts and self.connected:
                next_queued = rt.queued_prompts.pop(0)
                self._save_queue(sid, rt.queued_prompts)
                await self._broadcast_queue(sid)

            if next_queued is not None:
                await self._start_prompt_turn(
                    sid,
                    next_queued.get("text", ""),
                    display_text=next_queued.get("display_text"),
                    images=next_queued.get("images") or [],
                )
            else:
                await self.broadcast_status()
                await self.broadcast({"type": "sessions_changed"})
                label = rt.title or "Codex"
                non_viewers = self._clients_not_viewing_session(sid)
                if non_viewers:
                    status_msg = (
                        "执行出错"
                        if failed
                        else ("响应提前中断" if abnormal_stop else "已完成")
                    )
                    await self.broadcast(
                        {
                            "type": "toast",
                            "data": {
                                "level": "error" if (failed or abnormal_stop) else "info",
                                "message": f"后台会话「{label}」{status_msg}",
                            },
                        },
                        targets=non_viewers,
                    )
                push_task: Optional[asyncio.Task] = None
                if not was_cancelled:
                    if failed:
                        push_title = f"执行出错 · {label}"
                        push_body = str(rt.items[-1].get("text") or "任务执行失败")[:110] if rt.items else "任务执行失败"
                        push_tag = f"agy-err-{sid}"
                    elif abnormal_stop:
                        push_title = f"响应提前中断 · {label}"
                        push_body = early_stop_msg[:110]
                        push_tag = f"agy-warn-{sid}"
                    else:
                        last_asst = ""
                        for it in reversed(rt.items):
                            if it.get("type") == "assistant" and str(it.get("text") or "").strip():
                                last_asst = " ".join(str(it["text"]).split())[:110]
                                break
                        push_title = f"任务已完成 · {label}"
                        push_body = last_asst or "回复已生成，点击查看"
                        push_tag = f"agy-done-{sid}"
                    push_task = self._schedule_push_if_unseen(sid, title=push_title, body=push_body, tag=push_tag)
                config.trigger_deferred_restart(push_task=push_task)

    async def cancel_current_turn(self, sid: Optional[str] = None) -> None:
        target_sid = sid or self.session_id
        if not target_sid:
            return
        rt = self._runtimes.get(target_sid)
        if rt:
            rt.cancel_requested = True
            for key in list(rt.pending_permissions.keys()):
                await self.respond_permission(key, None, cancelled=True)
        turn_id = self._thread_turn_ids.get(target_sid)
        if turn_id:
            try:
                await self._send_request("turn/interrupt", {"threadId": target_sid, "turnId": turn_id}, timeout=5.0)
            except Exception as exc:
                logger.warning("Failed sending turn/interrupt for %s: %s", target_sid[:8], exc)

    async def _run_btw_task(self, btw_ctx: Dict[str, Any]) -> None:
        bid = btw_ctx["btw_id"]
        source_sid = btw_ctx["source_sid"]
        temp_sid = ""
        error_msg = ""
        try:
            await self.start()
            rt = self._runtimes.get(source_sid) if source_sid else None
            workdir = rt.workdir if rt else self.workdir
            model_id = (rt.current_model if rt else "") or self._fallback_model
            base_model, effort = self._split_model_and_effort(model_id)

            forked = False
            if source_sid:
                fork_params: Dict[str, Any] = {
                    "threadId": source_sid,
                    "cwd": workdir,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                }
                if base_model:
                    fork_params["model"] = base_model
                try:
                    fork_res = await self._send_request("thread/fork", fork_params, timeout=25.0)
                    temp_sid = str((fork_res.get("thread") or {}).get("id") or "").strip()
                    forked = bool(temp_sid)
                except Exception as exc:
                    logger.warning("Codex /btw thread/fork failed for %s (%s); falling back to thread/start", source_sid[:8], exc)

            if not temp_sid:
                start_params: Dict[str, Any] = {
                    "cwd": workdir,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                }
                if base_model:
                    start_params["model"] = base_model
                start_res = await self._send_request("thread/start", start_params, timeout=25.0)
                temp_sid = str((start_res.get("thread") or {}).get("id") or "").strip()

            if not temp_sid:
                raise RuntimeError("无法创建临时旁白线程")

            btw_ctx["temp_sid"] = temp_sid
            self._btw_sessions[temp_sid] = btw_ctx
            self._btw_id_to_sid[bid] = temp_sid

            if btw_ctx.get("cancelled"):
                return

            if forked:
                prompt_text = (
                    "[临时旁白提问 /btw]\n"
                    "请直接基于当前对话上下文简明回答以下问题，不要修改文件或执行写操作：\n\n"
                    f"{btw_ctx['question']}"
                )
            else:
                prompt_text = self._build_btw_fallback_prompt(btw_ctx["question"], source_sid)

            loop = asyncio.get_running_loop()
            turn_fut: asyncio.Future = loop.create_future()
            self._turn_futures[temp_sid] = turn_fut

            turn_params: Dict[str, Any] = {
                "threadId": temp_sid,
                "input": [{"type": "text", "text": prompt_text}],
                "cwd": workdir,
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly"},
            }
            if base_model:
                turn_params["model"] = base_model
            if effort:
                turn_params["effort"] = effort

            t_res = await self._send_request("turn/start", turn_params, timeout=30.0)
            turn_id = str((t_res.get("turn") or {}).get("id") or "")
            if turn_id:
                self._thread_turn_ids[temp_sid] = turn_id

            if btw_ctx.get("cancelled"):
                if turn_id:
                    try:
                        await self._send_request("turn/interrupt", {"threadId": temp_sid, "turnId": turn_id}, timeout=5.0)
                    except Exception:
                        pass
                return

            await asyncio.wait_for(turn_fut, timeout=180.0)
        except asyncio.CancelledError:
            btw_ctx["cancelled"] = True
        except Exception as exc:
            if not btw_ctx.get("cancelled"):
                error_msg = str(exc) or type(exc).__name__
                logger.warning("Codex /btw failed (%s): %s", bid, error_msg)
        finally:
            self._btw_id_to_sid.pop(bid, None)
            if temp_sid:
                self._btw_sessions.pop(temp_sid, None)
                self._turn_futures.pop(temp_sid, None)
                self._thread_turn_ids.pop(temp_sid, None)
                try:
                    await self._send_request("thread/archive", {"threadId": temp_sid}, timeout=2.5)
                except Exception:
                    pass
                try:
                    await self._send_request("thread/unsubscribe", {"threadId": temp_sid}, timeout=2.5)
                except Exception:
                    pass
            if not btw_ctx.get("cancelled"):
                await self._emit_btw_event(
                    btw_ctx,
                    {
                        "type": "btw_done",
                        "data": {
                            "btw_id": bid,
                            "text": btw_ctx.get("text", ""),
                            "error": error_msg,
                        },
                    },
                )

    async def cancel_btw(self, btw_id: str) -> bool:
        bid = str(btw_id or "").strip()
        if not bid:
            return False
        temp_sid = self._btw_id_to_sid.get(bid)
        if not temp_sid:
            return False
        btw_ctx = self._btw_sessions.get(temp_sid)
        if not btw_ctx:
            return False
        btw_ctx["cancelled"] = True
        turn_id = self._thread_turn_ids.get(temp_sid)
        if turn_id:
            try:
                await self._send_request("turn/interrupt", {"threadId": temp_sid, "turnId": turn_id}, timeout=5.0)
            except Exception:
                pass
        fut = self._turn_futures.get(temp_sid)
        if fut and not fut.done():
            fut.cancel()
        return True

    async def _stop_unlocked(self) -> None:
        if self._account_refresh_task and not self._account_refresh_task.done():
            self._account_refresh_task.cancel()
        self._account_refresh_task = None
        for fut in list(self._turn_futures.values()):
            if not fut.done():
                fut.cancel()
        self._turn_futures.clear()
        self._thread_turn_ids.clear()
        await super()._stop_unlocked()

    async def _handle_rpc_message(self, msg: Dict[str, Any]) -> None:
        if "id" in msg and "method" not in msg:
            req_id = msg["id"]
            fut = self._pending_rpc.get(req_id)
            if fut and not fut.done():
                if "error" in msg and msg["error"] is not None:
                    err = msg["error"]
                    fut.set_exception(RuntimeError(err.get("message", str(err))))
                else:
                    fut.set_result(msg.get("result", {}))
            return

        method = str(msg.get("method") or "")
        params = msg.get("params") or {}
        if "id" in msg and method:
            await self._handle_server_request(msg["id"], method, params)
            return
        await self._handle_codex_notification(method, params)

    async def _handle_server_request(self, req_id: Any, method: str, params: Dict[str, Any]) -> None:
        sid = str(params.get("threadId") or "").strip() or (self.session_id or "")
        key = str(req_id)
        if method in (
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "item/tool/requestUserInput",
        ):
            if sid and sid in self._btw_sessions:
                if method == "item/permissions/requestApproval":
                    res_payload: Dict[str, Any] = {"permissions": {}, "scope": "turn"}
                elif method == "item/tool/requestUserInput":
                    res_payload = {"answers": {}}
                else:
                    res_payload = {"decision": "decline"}
                await self._write_json({"jsonrpc": "2.0", "id": req_id, "result": res_payload})
                return
            if method == "item/commandExecution/requestApproval":
                title = str(params.get("command") or params.get("reason") or "执行命令审批")
                kind = "execute"
                args: Dict[str, Any] = {"command": params.get("command", ""), "cwd": params.get("cwd", ""), "reason": params.get("reason", "")}
                options = [
                    {"optionId": "accept", "name": "允许一次", "kind": "allow_once"},
                    {"optionId": "acceptForSession", "name": "本会话允许", "kind": "allow_always"},
                    {"optionId": "decline", "name": "拒绝", "kind": "reject_once"},
                ]
            elif method == "item/fileChange/requestApproval":
                title = str(params.get("reason") or "文件修改审批")
                kind = "edit"
                args = {"grantRoot": params.get("grantRoot", ""), "reason": params.get("reason", "")}
                options = [
                    {"optionId": "accept", "name": "允许一次", "kind": "allow_once"},
                    {"optionId": "acceptForSession", "name": "本会话允许", "kind": "allow_always"},
                    {"optionId": "decline", "name": "拒绝", "kind": "reject_once"},
                ]
            elif method == "item/permissions/requestApproval":
                title = str(params.get("reason") or "权限提升审批")
                kind = "other"
                args = {"permissions": params.get("permissions") or {}, "reason": params.get("reason", "")}
                options = [
                    {"optionId": "accept", "name": "允许本次", "kind": "allow_once"},
                    {"optionId": "acceptForSession", "name": "本会话允许", "kind": "allow_always"},
                    {"optionId": "decline", "name": "拒绝", "kind": "reject_once"},
                ]
            else:
                questions = params.get("questions") or []
                first_q = questions[0] if questions and isinstance(questions[0], dict) else {}
                title = str(first_q.get("question") or "Codex 请求输入")
                kind = "other"
                args = {"questions": questions}
                options = [
                    {"optionId": str(opt.get("label") or opt.get("value") or i), "name": str(opt.get("label") or opt.get("value") or i), "kind": "allow_once"}
                    for i, opt in enumerate(first_q.get("options") or [])
                    if isinstance(opt, dict)
                ] or [
                    {"optionId": "确认", "name": "确认", "kind": "allow_once"},
                    {"optionId": "取消", "name": "取消", "kind": "reject_once"},
                ]

            perm_record = {
                "key": key,
                "request_id": req_id,
                "session_id": sid,
                "codex_method": method,
                "codex_params": params,
                "tool_call": {
                    "toolCallId": str(params.get("itemId") or ""),
                    "name": method.split("/")[1] if "/" in method else method,
                    "title": title,
                    "kind": kind,
                    "args": args,
                },
                "options": options,
                "ts": time.time(),
            }
            if sid:
                rt = self._get_or_create_runtime(sid)
                rt.pending_permissions[key] = perm_record
                self._perm_to_sid[key] = sid
            else:
                self._fallback_permissions[key] = perm_record
            await self.broadcast({"type": "permission_request", "data": perm_record}, sid=sid or None)
            await self.broadcast_status()
            await self.broadcast({"type": "sessions_changed"})
            rt_title = self._runtimes[sid].title if sid in self._runtimes else "Codex"
            self._schedule_push_if_unseen(
                sid,
                title=f"需确认权限 · {rt_title}",
                body=title,
                tag=f"agy-perm-{sid}",
            )
            return

        await self._write_json(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not supported by client: {method}"},
            }
        )

    async def respond_permission(self, key: str, option_id: Optional[str], cancelled: bool = False) -> bool:
        skey = str(key)
        sid = self._perm_to_sid.pop(skey, None)
        perm = None
        if sid and sid in self._runtimes:
            perm = self._runtimes[sid].pending_permissions.pop(skey, None)
        if perm is None:
            for r in self._runtimes.values():
                if skey in r.pending_permissions:
                    perm = r.pending_permissions.pop(skey, None)
                    sid = r.session_id
                    break
        if perm is None:
            perm = self._fallback_permissions.pop(skey, None)
        if not perm:
            return False

        cmethod = perm.get("codex_method") or ""
        cparams = perm.get("codex_params") or {}
        if cmethod == "item/permissions/requestApproval":
            if cancelled or not option_id or option_id == "decline":
                result_payload = {"permissions": {}, "scope": "turn"}
            else:
                result_payload = {
                    "permissions": cparams.get("permissions") or {},
                    "scope": "session" if option_id == "acceptForSession" else "turn",
                }
        elif cmethod == "item/tool/requestUserInput":
            answers: Dict[str, Any] = {}
            if not cancelled and option_id:
                for q in cparams.get("questions") or []:
                    if isinstance(q, dict) and q.get("id"):
                        answers[str(q["id"])] = {"answers": [str(option_id)]}
            result_payload = {"answers": answers}
        else:
            decision = "decline" if (cancelled or not option_id) else str(option_id)
            result_payload = {"decision": decision}

        await self._write_json({"jsonrpc": "2.0", "id": perm["request_id"], "result": result_payload})
        await self.broadcast(
            {
                "type": "permission_resolved",
                "data": {"key": skey, "option_id": option_id, "cancelled": cancelled},
            },
            sid=sid or None,
        )
        await self.broadcast_status()
        await self.broadcast({"type": "sessions_changed"})
        return True

    async def _handle_codex_notification(self, method: str, params: Dict[str, Any]) -> None:
        sid = str(params.get("threadId") or "").strip() or (self.session_id or "")

        # Ephemeral /btw side-question stream: isolate completely from main runtimes & SQLite
        btw_ctx = self._btw_sessions.get(sid) if sid else None
        if btw_ctx is not None:
            if method == "item/agentMessage/delta" and not btw_ctx.get("cancelled"):
                delta = str(params.get("delta") or "")
                if delta:
                    btw_ctx["text"] += delta
                    await self._emit_btw_event(
                        btw_ctx,
                        {"type": "btw_delta", "data": {"btw_id": btw_ctx["btw_id"], "delta": delta}},
                    )
            elif method == "item/completed" and not btw_ctx.get("cancelled"):
                raw_item = params.get("item") or {}
                ui_item = self._convert_codex_item_to_ui(raw_item)
                if ui_item and ui_item.get("type") == "assistant" and ui_item.get("text") and not btw_ctx.get("text"):
                    text = str(ui_item["text"])
                    btw_ctx["text"] = text
                    await self._emit_btw_event(
                        btw_ctx,
                        {"type": "btw_delta", "data": {"btw_id": btw_ctx["btw_id"], "delta": text}},
                    )
            elif method == "turn/completed":
                fut = self._turn_futures.get(sid)
                if fut and not fut.done():
                    fut.set_result(params.get("turn") or {})
            elif method == "error" and not params.get("willRetry"):
                err_obj = params.get("error") or {}
                fut = self._turn_futures.get(sid)
                if fut and not fut.done():
                    fut.set_exception(RuntimeError(str(err_obj.get("message") or "Codex error")))
            return

        rt = self._get_or_create_runtime(sid) if sid else None
        target_sid = sid or None

        if method == "item/agentMessage/delta":
            if not rt:
                return
            delta = str(params.get("delta") or "")
            if not delta:
                return
            rt.active_thought_id = None
            raw_iid = str(params.get("itemId") or "")
            item_id = f"asst_{raw_iid}" if raw_iid else (rt.active_assistant_id or f"asst_{uuid.uuid4().hex[:12]}")
            rt.active_assistant_id = item_id
            existing = rt.items_by_id.get(item_id)
            if existing:
                existing["text"] += delta
                self._add_or_update_item(existing, sid=sid)
            else:
                self._add_or_update_item({"id": item_id, "type": "assistant", "text": delta, "ts": time.time()}, sid=sid)
            await self.broadcast({"type": "text_delta", "data": {"item_id": item_id, "delta": delta}}, sid=target_sid)

        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            if not rt:
                return
            delta = str(params.get("delta") or "")
            if not delta:
                return
            rt.active_assistant_id = None
            raw_iid = str(params.get("itemId") or "")
            item_id = f"thought_{raw_iid}" if raw_iid else (rt.active_thought_id or f"thought_{uuid.uuid4().hex[:12]}")
            rt.active_thought_id = item_id
            existing = rt.items_by_id.get(item_id)
            if existing:
                existing["text"] += delta
                self._add_or_update_item(existing, sid=sid)
            else:
                self._add_or_update_item({"id": item_id, "type": "thought", "text": delta, "ts": time.time()}, sid=sid)
            await self.broadcast({"type": "thought_delta", "data": {"item_id": item_id, "delta": delta}}, sid=target_sid)

        elif method == "item/commandExecution/outputDelta":
            if not rt:
                return
            raw_iid = str(params.get("itemId") or "")
            delta = str(params.get("delta") or "")
            if not raw_iid or not delta:
                return
            item_id = f"tool_{raw_iid}"
            existing = rt.items_by_id.get(item_id)
            if existing:
                existing["output"] = str(existing.get("output") or "") + delta
                self._add_or_update_item(existing, sid=sid)
                await self.broadcast({"type": "item_upsert", "data": existing}, sid=target_sid)

        elif method in ("item/started", "item/completed"):
            if not rt:
                return
            raw_item = params.get("item") or {}
            ui_item = self._convert_codex_item_to_ui(raw_item)
            if not ui_item:
                return
            if ui_item["type"] == "tool":
                rt.active_assistant_id = None
                rt.active_thought_id = None
                existing = rt.items_by_id.get(ui_item["id"])
                if existing and not ui_item.get("output") and existing.get("output"):
                    ui_item["output"] = existing["output"]
                self._add_or_update_item(ui_item, sid=sid)
                await self.broadcast({"type": "item_upsert", "data": ui_item}, sid=target_sid)
            elif ui_item["type"] == "assistant" and method == "item/completed":
                if ui_item.get("text"):
                    existing = rt.items_by_id.get(ui_item["id"])
                    if not existing:
                        self._add_or_update_item(ui_item, sid=sid)
                        await self.broadcast({"type": "item_upsert", "data": ui_item}, sid=target_sid)
                rt.active_assistant_id = None
            elif ui_item["type"] == "thought" and method == "item/completed":
                if ui_item.get("text") and not rt.items_by_id.get(ui_item["id"]):
                    self._add_or_update_item(ui_item, sid=sid)
                    await self.broadcast({"type": "item_upsert", "data": ui_item}, sid=target_sid)
                rt.active_thought_id = None

        elif method == "thread/tokenUsage/updated":
            tu = params.get("tokenUsage") or {}
            last_tu = tu.get("last") or tu.get("total") or {}
            new_usage = {
                "used": int(last_tu.get("totalTokens") or 0),
                "size": int(tu.get("modelContextWindow") or 0),
            }
            if rt:
                rt.usage = new_usage
                self._save_usage(sid, new_usage)
            await self.broadcast({"type": "usage", "data": new_usage}, sid=target_sid)

        elif method == "thread/name/updated":
            title = str(params.get("threadName") or "").strip()
            if title and rt:
                rt.title = title
                self.store.upsert_session(sid, rt.workdir, title, profile=self.current_profile)
                if sid == self.session_id:
                    self.store.set_meta("session_title", title)
                await self.broadcast_status()
                await self.broadcast({"type": "sessions_changed"})

        elif method == "account/rateLimits/updated":
            self._codex_rate_limits = params.get("rateLimits")

        elif method == "thread/compacted":
            if sid in self._compact_sids:
                fut = self._turn_futures.get(sid)
                if fut and not fut.done():
                    fut.set_result({"status": "completed"})

        elif method == "turn/completed":
            self._invalidate_thread_cache()
            fut = self._turn_futures.get(sid)
            if fut and not fut.done():
                fut.set_result(params.get("turn") or {})

        elif method == "error":
            if not params.get("willRetry"):
                err_obj = params.get("error") or {}
                fut = self._turn_futures.get(sid)
                if fut and not fut.done():
                    fut.set_exception(RuntimeError(str(err_obj.get("message") or "Codex error")))

    async def fetch_quota(self) -> Dict[str, Any]:
        if not self.connected:
            raise RuntimeError("Codex 尚未连接")
        res = await self._send_request("account/rateLimits/read", {}, timeout=10.0)
        snapshots: List[Dict[str, Any]] = []
        by_id = res.get("rateLimitsByLimitId")
        if isinstance(by_id, dict) and by_id:
            snapshots = [v for v in by_id.values() if isinstance(v, dict)]
        elif isinstance(res.get("rateLimits"), dict):
            snapshots = [res["rateLimits"]]

        reset_credits = res.get("rateLimitResetCredits") or {}
        avail_resets = int(reset_credits.get("availableCount") or 0) if isinstance(reset_credits, dict) else 0

        summary: List[Dict[str, Any]] = []
        for idx, snap in enumerate(snapshots):
            buckets: List[Dict[str, Any]] = []
            for key in ("primary", "secondary"):
                win = snap.get(key)
                if not isinstance(win, dict):
                    continue
                mins = int(win.get("windowDurationMins") or 0)
                if mins and mins <= 360:
                    w_name = "5h"
                elif mins >= 10000:
                    w_name = "weekly"
                else:
                    w_name = f"{mins}m" if mins else key
                used_pct = float(win.get("usedPercent") or 0)
                rem = max(0.0, min(1.0, 1.0 - used_pct / 100.0))
                resets_at = win.get("resetsAt")
                reset_iso = ""
                if isinstance(resets_at, (int, float)) and resets_at > 0:
                    reset_iso = datetime.fromtimestamp(float(resets_at), tz=timezone.utc).isoformat()
                buckets.append({"window": w_name, "label": w_name, "remaining": rem, "reset_time": reset_iso})
            if buckets:
                plan = str(snap.get("planType") or self._codex_account.get("planType") or "").strip()
                raw_name = str(snap.get("limitName") or "").strip()
                if raw_name:
                    name = raw_name
                elif plan:
                    name = f"Codex {plan.capitalize()} 额度"
                else:
                    name = "Codex 额度"
                members: List[str] = []
                credits_info = snap.get("credits")
                if isinstance(credits_info, dict):
                    if credits_info.get("unlimited"):
                        members.append("无限额度")
                    elif credits_info.get("hasCredits") and credits_info.get("balance"):
                        members.append(f"Credits 余额: {credits_info['balance']}")
                if idx == 0 and avail_resets > 0:
                    members.append(f"免费额度重置次数: {avail_resets} 次")
                summary.append({"name": name, "members": members, "buckets": buckets})
        return {"profile": self.current_profile, "endpoint": "codex-app-server", "summary": summary, "groups": []}


