import asyncio
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .engines.pool import ProfileManagerPool

logger = config.logger

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_URL_RE = re.compile(r"https://[^\s<>'\"`]+")
_DEVICE_CODE_RE = re.compile(r"\b([A-Z0-9]{3,6}-[A-Z0-9]{3,6})\b")
_DEVICE_CODE_LABEL_RE = re.compile(
    r"(?:code|验证码|device\s*code)[^A-Z0-9]{0,12}([A-Z0-9]{3,6}-[A-Z0-9]{3,6})",
    re.IGNORECASE,
)

FLOW_TIMEOUT_SECONDS = 600.0
MAX_RETAINED_FLOWS = 30


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text or "")


def extract_auth_url(text: str) -> str:
    cleaned = strip_ansi(text)
    for match in _URL_RE.findall(cleaned):
        url = match.rstrip(".,;:)")
        if url:
            return url
    return ""


def extract_device_code(text: str) -> str:
    cleaned = strip_ansi(text)
    # Remove URLs first so query/path tokens never masquerade as device codes
    without_urls = _URL_RE.sub(" ", cleaned)
    labeled = _DEVICE_CODE_LABEL_RE.search(without_urls)
    if labeled:
        return labeled.group(1).upper()
    for line in without_urls.splitlines():
        s = line.strip()
        if not s:
            continue
        # Skip timestamps or version-like lines
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            continue
        for cand in _DEVICE_CODE_RE.findall(s):
            if any(ch.isalpha() for ch in cand) and any(ch.isdigit() for ch in cand):
                return cand
        # Some device codes are all-uppercase letters like ABCD-EFGH
        m_full = re.fullmatch(r"([A-Z0-9]{4,5}-[A-Z0-9]{4,5})", s)
        if m_full:
            return m_full.group(1)
    return ""


@dataclass
class ProfileAuthFlow:
    flow_id: str
    profile: str
    backend: str  # "codex" | "agy"
    status: str = "starting"  # starting | awaiting_user | submitting | reloading | succeeded | failed | cancelled
    auth_url: str = ""
    user_code: str = ""
    needs_code_input: bool = False
    message: str = "正在启动登录流程..."
    error: str = ""
    email: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    proc: Optional[asyncio.subprocess.Process] = None
    task: Optional[asyncio.Task] = None
    output_lines: List[str] = field(default_factory=list)
    cancelled: bool = False

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "profile": self.profile,
            "backend": self.backend,
            "status": self.status,
            "auth_url": self.auth_url,
            "user_code": self.user_code,
            "needs_code_input": self.needs_code_input,
            "message": self.message,
            "error": self.error,
            "email": self.email,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProfileAuthFlowManager:
    """Manages interactive CLI login flows for Codex (`--device-auth`) and AGY (`acp --login`)."""

    def __init__(self, pool: ProfileManagerPool):
        self.pool = pool
        self._flows: Dict[str, ProfileAuthFlow] = {}

    def _prune_old_flows(self) -> None:
        if len(self._flows) <= MAX_RETAINED_FLOWS:
            return
        terminal = [
            f
            for f in self._flows.values()
            if f.status in ("succeeded", "failed", "cancelled")
        ]
        terminal.sort(key=lambda f: f.updated_at)
        for f in terminal[: max(0, len(self._flows) - MAX_RETAINED_FLOWS)]:
            self._flows.pop(f.flow_id, None)

    @staticmethod
    def _build_env_for_home(home_dir: str, codex_home: Optional[str] = None) -> Dict[str, str]:
        env = dict(os.environ)
        env["HOME"] = home_dir
        env["NO_COLOR"] = "1"
        env["CLICOLOR"] = "0"
        if codex_home:
            env["CODEX_HOME"] = codex_home
        return env

    async def _terminate_proc(self, proc: Optional[asyncio.subprocess.Process]) -> None:
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except Exception:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception:
                pass

    async def _cancel_active_for_profile(self, profile: str) -> None:
        for flow in list(self._flows.values()):
            if flow.profile == profile and flow.status in ("starting", "awaiting_user", "submitting"):
                await self.cancel_flow(flow.flow_id)

    async def start_login(
        self,
        profile: str,
        *,
        backend: Optional[str] = None,
        create_if_missing: bool = False,
        logout_first: bool = False,
    ) -> Dict[str, Any]:
        prof = str(profile or "").strip()
        if not prof:
            raise ValueError("请输入账号名称")

        req_backend = str(backend or "").strip().lower()
        if req_backend == "codex":
            is_codex = True
        elif req_backend == "agy":
            is_codex = False
        else:
            is_codex = config.is_codex_profile(prof)

        codex_home_str: Optional[str] = None
        if not is_codex:
            if prof == config.CODEX_PROFILE_NAME or (config.CODEX_PROFILES_DIR / prof).is_dir():
                raise ValueError(f"账号名 {prof} 已被 Codex 账号占用")
            if prof not in ("agy", "default") and not config._is_valid_profile_name(prof):
                raise ValueError("账号名称仅支持字母、数字、下划线和短横线")
            if create_if_missing and prof not in ("agy", "default"):
                target_dir = config.AGY_PROFILES_DIR / prof
                target_dir.mkdir(parents=True, exist_ok=True)
            home_path = config.ensure_profile_home(prof)
        else:
            if prof != config.CODEX_PROFILE_NAME:
                if prof in ("agy", "default") or (config.AGY_PROFILES_DIR / prof).is_dir():
                    raise ValueError(f"账号名 {prof} 已被 AGY 账号占用")
                if not config._is_valid_profile_name(prof):
                    raise ValueError("账号名称仅支持字母、数字、下划线和短横线")
                if create_if_missing:
                    codex_home = config.ensure_codex_home(prof)
                else:
                    codex_home = config.resolve_codex_home(prof)
                    if not codex_home.is_dir():
                        raise ValueError(f"Codex 账号配置目录不存在: {prof}")
            else:
                codex_home = config.ensure_codex_home(prof)
            codex_home_str = str(codex_home)
            home_path = config.REAL_HOME

        await self._cancel_active_for_profile(prof)
        self._prune_old_flows()

        flow_id = f"auth_{uuid.uuid4().hex[:12]}"
        flow = ProfileAuthFlow(
            flow_id=flow_id,
            profile=prof,
            backend="codex" if is_codex else "agy",
            needs_code_input=(not is_codex),
            message="正在启动 Codex 设备码登录..." if is_codex else "正在生成 Google OAuth 登录链接...",
        )
        self._flows[flow_id] = flow

        if is_codex:
            codex_bin = config.resolve_codex_binary()
            if not codex_bin:
                flow.status = "failed"
                flow.error = "未检测到 codex CLI，请先安装 @openai/codex"
                flow.message = flow.error
                flow.updated_at = time.time()
                return flow.to_public_dict()
            flow.task = asyncio.create_task(
                self._run_codex_flow(
                    flow,
                    codex_bin,
                    str(home_path),
                    codex_home=codex_home_str,
                    logout_first=logout_first,
                ),
                name=f"auth-codex-{flow_id}",
            )
        else:
            try:
                agy_bin = config.resolve_agy_binary(for_profile=prof)
            except Exception as exc:
                flow.status = "failed"
                flow.error = str(exc)
                flow.message = flow.error
                flow.updated_at = time.time()
                return flow.to_public_dict()
            flow.task = asyncio.create_task(
                self._run_agy_flow(flow, agy_bin, str(home_path)),
                name=f"auth-agy-{flow_id}",
            )

        # Wait briefly (up to 4s) so the initial response often already carries auth_url / user_code
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if flow.status != "starting":
                break
            await asyncio.sleep(0.08)

        return flow.to_public_dict()

    async def _stream_process_output(
        self,
        flow: ProfileAuthFlow,
        proc: asyncio.subprocess.Process,
        *,
        parse_device_code: bool,
    ) -> Tuple[int, List[str]]:
        lines: List[str] = []
        assert proc.stdout is not None
        deadline = time.monotonic() + FLOW_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await self._terminate_proc(proc)
                raise TimeoutError("登录等待超时（10 分钟），请重试")
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, 1.0))
            except asyncio.TimeoutError:
                if proc.returncode is not None:
                    break
                continue
            if not raw:
                break
            line = strip_ansi(raw.decode("utf-8", errors="replace")).rstrip("\r\n")
            if line.strip():
                lines.append(line.strip())
                flow.output_lines = lines[-20:]
            if not flow.auth_url:
                url = extract_auth_url(line)
                if url:
                    flow.auth_url = url
                    flow.updated_at = time.time()
            if parse_device_code and not flow.user_code:
                code = extract_device_code(line)
                if code:
                    flow.user_code = code
                    flow.updated_at = time.time()
            if flow.status == "starting":
                if parse_device_code and (flow.auth_url and flow.user_code):
                    flow.status = "awaiting_user"
                    flow.message = "请在浏览器打开授权链接并输入下方设备码"
                    flow.updated_at = time.time()
                elif (not parse_device_code) and flow.auth_url:
                    flow.status = "awaiting_user"
                    flow.message = "请在浏览器完成 Google 登录，然后将授权码或完整回调链接粘贴到下方提交"
                    flow.updated_at = time.time()

        rc = await proc.wait()
        return rc, lines

    async def _finish_login_and_reload(self, flow: ProfileAuthFlow) -> None:
        flow.status = "reloading"
        flow.message = f"登录成功，正在热重载账号 {flow.profile}..."
        flow.updated_at = time.time()
        try:
            res = await self.pool.reload_profile(flow.profile, force=True)
            flow.email = res.get("email")
            flow.status = "succeeded"
            flow.message = (
                f"账号 {flow.profile} 登录并热重载成功（{flow.email}）"
                if flow.email
                else f"账号 {flow.profile} 登录并热重载成功"
            )
        except Exception as exc:
            logger.warning("Login succeeded for %s but reload failed: %s", flow.profile, exc)
            flow.status = "succeeded"
            flow.message = f"登录凭证已保存，但自动重载失败（{exc}），可点击手动重载"
        flow.updated_at = time.time()

    async def _run_codex_flow(
        self,
        flow: ProfileAuthFlow,
        codex_bin: str,
        home_dir: str,
        *,
        codex_home: Optional[str] = None,
        logout_first: bool = False,
    ) -> None:
        env = self._build_env_for_home(home_dir, codex_home=codex_home)
        try:
            if logout_first:
                logout_proc = await asyncio.create_subprocess_exec(
                    codex_bin,
                    "logout",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                )
                try:
                    await asyncio.wait_for(logout_proc.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    await self._terminate_proc(logout_proc)

            if flow.cancelled:
                return

            proc = await asyncio.create_subprocess_exec(
                codex_bin,
                "login",
                "--device-auth",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            flow.proc = proc
            rc, lines = await self._stream_process_output(flow, proc, parse_device_code=True)
            if flow.cancelled:
                return
            if rc == 0:
                await self._finish_login_and_reload(flow)
            else:
                tail = " | ".join(lines[-3:]) if lines else f"退出码 {rc}"
                flow.status = "failed"
                flow.error = f"Codex 登录失败: {tail}"
                flow.message = flow.error
                flow.updated_at = time.time()
        except asyncio.CancelledError:
            await self._terminate_proc(flow.proc)
            if not flow.cancelled:
                flow.cancelled = True
                flow.status = "cancelled"
                flow.message = "已取消登录"
                flow.updated_at = time.time()
            raise
        except Exception as exc:
            if flow.cancelled:
                return
            flow.status = "failed"
            flow.error = str(exc)
            flow.message = flow.error
            flow.updated_at = time.time()
        finally:
            flow.proc = None

    async def _run_agy_flow(
        self,
        flow: ProfileAuthFlow,
        agy_bin: str,
        home_dir: str,
    ) -> None:
        env = self._build_env_for_home(home_dir)
        try:
            proc = await asyncio.create_subprocess_exec(
                agy_bin,
                "acp",
                "--login",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            flow.proc = proc
            rc, lines = await self._stream_process_output(flow, proc, parse_device_code=False)
            if flow.cancelled:
                return
            if rc == 0:
                await self._finish_login_and_reload(flow)
            else:
                tail = " | ".join(lines[-3:]) if lines else f"退出码 {rc}"
                flow.status = "failed"
                flow.error = f"AGY 登录失败: {tail}"
                flow.message = flow.error
                flow.updated_at = time.time()
        except asyncio.CancelledError:
            await self._terminate_proc(flow.proc)
            if not flow.cancelled:
                flow.cancelled = True
                flow.status = "cancelled"
                flow.message = "已取消登录"
                flow.updated_at = time.time()
            raise
        except Exception as exc:
            if flow.cancelled:
                return
            flow.status = "failed"
            flow.error = str(exc)
            flow.message = flow.error
            flow.updated_at = time.time()
        finally:
            flow.proc = None

    async def submit_code(self, flow_id: str, code_or_url: str) -> Dict[str, Any]:
        flow = self._flows.get(flow_id)
        if not flow:
            raise KeyError("登录会话不存在或已过期")
        if flow.backend != "agy":
            raise ValueError("当前登录流程不需要手动提交授权码")
        if flow.status not in ("awaiting_user", "starting"):
            raise ValueError(f"当前登录状态不可提交（{flow.status}）")
        cleaned = str(code_or_url or "").strip()
        if not cleaned:
            raise ValueError("请粘贴授权码或完整回调链接")
        proc = flow.proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            raise RuntimeError("登录进程已结束，请重新发起登录")

        flow.status = "submitting"
        flow.message = "正在提交授权码并验证凭证..."
        flow.updated_at = time.time()
        try:
            proc.stdin.write((cleaned + "\n").encode("utf-8"))
            await proc.stdin.drain()
        except Exception as exc:
            flow.status = "failed"
            flow.error = f"提交授权码失败: {exc}"
            flow.message = flow.error
            flow.updated_at = time.time()
            raise RuntimeError(flow.error) from exc

        # Wait briefly for fast completion so the UI gets immediate feedback when possible
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if flow.status in ("succeeded", "failed", "cancelled"):
                break
            await asyncio.sleep(0.08)

        return flow.to_public_dict()

    def get_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        flow = self._flows.get(flow_id)
        return flow.to_public_dict() if flow else None

    async def cancel_flow(self, flow_id: str) -> Dict[str, Any]:
        flow = self._flows.get(flow_id)
        if not flow:
            raise KeyError("登录会话不存在")
        flow.cancelled = True
        if flow.status in ("starting", "awaiting_user", "submitting"):
            flow.status = "cancelled"
            flow.message = "已取消登录"
            flow.updated_at = time.time()
        await self._terminate_proc(flow.proc)
        if flow.task and not flow.task.done():
            flow.task.cancel()
        return flow.to_public_dict()

    async def shutdown(self) -> None:
        for flow_id in list(self._flows.keys()):
            try:
                await self.cancel_flow(flow_id)
            except Exception:
                pass
