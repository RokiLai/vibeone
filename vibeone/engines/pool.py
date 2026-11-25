import asyncio
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import WebSocket

from .. import config, quota
from ..push import WebPushManager
from ..store import SessionStore
from .acp import AgyACPManager
from .codex import CodexAppServerManager

logger = config.logger

class ProfileManagerPool:
    """Lazy warm-pool of per-profile AgyACPManager instances for instant (<50ms) switching."""

    def __init__(self, workdir: str, store: SessionStore, push: Optional[WebPushManager] = None):
        self.default_workdir = workdir
        self.store = store
        self.push = push
        self.clients: Set[WebSocket] = set()
        self._client_visible: Dict[WebSocket, bool] = {}
        self._client_views: Dict[WebSocket, Dict[str, Optional[str]]] = {}
        self._profile_locks: Dict[str, asyncio.Lock] = {}
        self._managers: Dict[str, AgyACPManager] = {}
        self._prewarm_tasks: Dict[str, asyncio.Task] = {}

        saved = str(self.store.get_meta("active_profile") or "").strip()
        if saved:
            try:
                config.resolve_profile_home(saved)
                self.current_profile = saved
            except Exception:
                self.current_profile = config.DEFAULT_AGY_PROFILE
        else:
            self.current_profile = config.DEFAULT_AGY_PROFILE

        self._get_or_create(self.current_profile)

    def _create_manager(self, profile: str) -> AgyACPManager:
        cls = CodexAppServerManager if config.is_codex_profile(profile) else AgyACPManager
        return cls(
            self.default_workdir,
            self.store,
            push=self.push,
            profile=profile,
            clients=self.clients,
            client_visible=self._client_visible,
            client_views=self._client_views,
            is_active_fn=lambda p=profile: self.current_profile == p,
        )

    def _get_or_create(self, profile: str) -> AgyACPManager:
        mgr = self._managers.get(profile)
        if mgr is None:
            mgr = self._create_manager(profile)
            self._managers[profile] = mgr
        return mgr

    def get_manager(self, profile: Optional[str] = None) -> AgyACPManager:
        p = str(profile or "").strip()
        if not p:
            return self.active
        if p in self._managers:
            return self._managers[p]
        config.resolve_profile_home(p)
        return self._get_or_create(p)

    def set_client_view(self, ws: WebSocket, profile: str, session_id: Optional[str]) -> None:
        self._client_views[ws] = {"profile": profile, "session_id": session_id}

    def get_client_view(self, ws: WebSocket) -> Tuple[str, Optional[str]]:
        view = self._client_views.get(ws)
        if view and view.get("profile"):
            prof = str(view["profile"])
            sid = view.get("session_id")
            return prof, (str(sid) if sid else None)
        return self.current_profile, self.active.session_id

    def remove_client(self, ws: WebSocket) -> None:
        self.clients.discard(ws)
        self._client_visible.pop(ws, None)
        self._client_views.pop(ws, None)

    @property
    def active(self) -> AgyACPManager:
        return self._get_or_create(self.current_profile)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.active, name)

    @staticmethod
    def _is_warm(mgr: Optional[AgyACPManager]) -> bool:
        return bool(mgr and mgr.connected and mgr.proc and mgr.proc.returncode is None)

    def _get_profile_lock(self, profile: str) -> asyncio.Lock:
        lock = self._profile_locks.get(profile)
        if lock is None:
            lock = asyncio.Lock()
            self._profile_locks[profile] = lock
        return lock

    async def start(self) -> None:
        await self.active.start()

    async def _notify_profile_ready(self, profile: str, target: AgyACPManager) -> None:
        if not self.clients:
            return
        targets: List[WebSocket] = []
        for ws in list(self.clients):
            view = self._client_views.get(ws)
            if view and view.get("profile") == profile:
                if not view.get("session_id") and target.session_id:
                    view["session_id"] = target.session_id
                targets.append(ws)
            elif not view and self.current_profile == profile:
                targets.append(ws)
        if not targets:
            return
        for ws in targets:
            view = self._client_views.get(ws)
            sid = (view.get("session_id") if view else None) or target.session_id
            await target.broadcast(
                {"type": "snapshot", "data": target.get_snapshot(sid=sid)},
                targets=[ws],
            )
        await target.broadcast({"type": "sessions_changed"}, targets=targets)

    async def prewarm_profile(
        self,
        profile: str,
        *,
        discovered_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> bool:
        profile = str(profile or "").strip()
        if not profile:
            return False
        if discovered_profiles is None:
            profiles = {p["name"]: p for p in config.list_agy_profiles(self.current_profile)}
        else:
            profiles = discovered_profiles
        info = profiles.get(profile)
        if not info or not info.get("logged_in"):
            logger.info("Skipping startup for profile %s (unavailable or not logged in)", profile)
            return False

        existing = self._managers.get(profile)
        if self._is_warm(existing):
            return True

        target = existing if existing is not None else self._get_or_create(profile)
        logger.info("Background starting profile %s...", profile)
        try:
            await target.start()
            logger.info("Background startup ready for profile %s (hot-switch enabled)", profile)
            await self._notify_profile_ready(profile, target)
            return True
        except asyncio.CancelledError:
            logger.info("Background startup cancelled for profile %s", profile)
            raise
        except Exception as exc:
            exc_msg = str(exc) or type(exc).__name__
            logger.warning(
                "Background startup failed for profile %s (will retry on demand): %s",
                profile,
                exc_msg,
            )
            if self.current_profile == profile:
                target.last_error = f"{profile} 初始化失败: {exc_msg}"
                try:
                    await target.broadcast_status()
                except Exception:
                    pass
            elif (
                self._managers.get(profile) is target
                and not self._is_warm(target)
            ):
                self._managers.pop(profile, None)
            return False

    def schedule_prewarm(self, profiles: Optional[List[str]] = None) -> List[asyncio.Task]:
        discovered_list = config.list_agy_profiles(self.current_profile)
        discovered = {p["name"]: p for p in discovered_list}
        targets = (
            config.get_prewarm_profiles(discovered_profiles=discovered_list)
            if profiles is None
            else list(profiles)
        )
        scheduled: List[asyncio.Task] = []
        for name in targets:
            profile = str(name or "").strip()
            if not profile:
                continue
            prev = self._prewarm_tasks.get(profile)
            if prev is not None and not prev.done():
                scheduled.append(prev)
                continue
            if self._is_warm(self._managers.get(profile)):
                continue
            task = asyncio.create_task(
                self.prewarm_profile(profile, discovered_profiles=discovered),
                name=f"prewarm-{profile}",
            )
            self._prewarm_tasks[profile] = task
            task.add_done_callback(
                lambda t, p=profile: self._prewarm_tasks.pop(p, None)
                if self._prewarm_tasks.get(p) is t
                else None
            )
            scheduled.append(task)
        return scheduled

    async def stop(self) -> None:
        tasks = [t for t in self._prewarm_tasks.values() if not t.done()]
        self._prewarm_tasks.clear()
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if not self._managers:
            return
        await asyncio.gather(
            *[m.stop() for m in list(self._managers.values())],
            return_exceptions=True,
        )

    def any_busy(self) -> bool:
        return any(
            rt.is_busy
            for mgr in self._managers.values()
            for rt in mgr._runtimes.values()
        )

    def get_profiles_info(self, active_profile: Optional[str] = None) -> List[Dict[str, Any]]:
        cur = str(active_profile or "").strip() or self.current_profile
        profiles = config.list_agy_profiles(cur)
        for p in profiles:
            mgr = self._managers.get(p["name"])
            p["warm"] = self._is_warm(mgr)
            p["busy_count"] = (
                sum(1 for rt in mgr._runtimes.values() if rt.is_busy) if mgr else 0
            )
            if mgr and not p.get("email"):
                p["email"] = mgr._current_profile_email()
        return profiles

    async def ensure_profile_ready(self, profile: str) -> Tuple[AgyACPManager, bool]:
        profile = str(profile or "").strip()
        config.resolve_profile_home(profile)
        profiles = {p["name"]: p for p in config.list_agy_profiles(self.current_profile)}
        info = profiles.get(profile)
        if not info:
            raise ValueError(f"未找到账号配置: {profile}")
        if not info.get("logged_in"):
            raise ValueError(f"账号 {profile} 尚未登录，请先在终端登录该账号")

        async with self._get_profile_lock(profile):
            existing = self._managers.get(profile)
            if self._is_warm(existing) and existing is not None:
                return existing, True

            # Cold-start target profile WITHOUT touching any other running profile
            target = existing if existing is not None else self._create_manager(profile)
            try:
                await target.start()
            except Exception as exc:
                exc_msg = str(exc) or type(exc).__name__
                logger.warning("Cold-starting profile %s failed: %s", profile, exc_msg)
                if existing is None:
                    try:
                        await target.stop()
                    except Exception:
                        pass
                err_hint = (
                    "认证已过期或未登录，请先在电脑终端重新登录"
                    if "Authentication required" in exc_msg
                    else exc_msg
                )
                raise RuntimeError(
                    f"切换到账号 {profile} 失败（{err_hint}），当前仍保持在 {self.current_profile}"
                ) from exc

            self._managers[profile] = target
            return target, False

    async def switch_profile(
        self,
        profile: str,
        force: bool = False,
        *,
        broadcast_all: bool = True,
    ) -> Dict[str, Any]:
        profile = str(profile or "").strip()
        target, is_hot = await self.ensure_profile_ready(profile)

        if not broadcast_all:
            # Per-client switch: do NOT mutate global broadcast or disturb other connected tabs
            logger.info(
                "%s-prepared profile %s for client-scoped switch",
                "Hot" if is_hot else "Cold",
                profile,
            )
            return {
                "ok": True,
                "hot": is_hot,
                "profile": profile,
                "email": target._current_profile_email(),
                "session_id": target.session_id,
            }

        prev = self.current_profile
        self.current_profile = profile
        self.store.set_meta("active_profile", profile)
        quota.clear_quota_cache()
        if prev != profile or not is_hot:
            await target.broadcast({"type": "snapshot", "data": target.get_snapshot()})
            await target.broadcast({"type": "sessions_changed"})
            logger.info(
                "%s-switched active profile to %s%s",
                "Hot" if is_hot else "Cold",
                profile,
                "" if is_hot else " (now warm in pool)",
            )
        return {
            "ok": True,
            "hot": is_hot,
            "profile": self.current_profile,
            "email": target._current_profile_email(),
            "session_id": target.session_id,
        }

    async def reload_profile(
        self,
        profile: str,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        profile = str(profile or "").strip()
        if not profile:
            raise ValueError("缺少账号名称")
        config.resolve_profile_home(profile)

        prewarm = self._prewarm_tasks.pop(profile, None)
        if prewarm is not None and not prewarm.done():
            prewarm.cancel()
            try:
                await prewarm
            except Exception:
                pass

        async with self._get_profile_lock(profile):
            existing = self._managers.get(profile)
            if existing is not None:
                busy_count = sum(1 for rt in existing._runtimes.values() if rt.is_busy)
                if busy_count > 0 and not force:
                    return {
                        "ok": False,
                        "needs_confirm": True,
                        "busy_count": busy_count,
                        "profile": profile,
                    }
                try:
                    await existing.stop()
                except Exception as exc:
                    logger.warning("Stopping profile %s during reload failed: %s", profile, exc)
                if self._managers.get(profile) is existing:
                    self._managers.pop(profile, None)

            quota.clear_quota_cache()
            profiles = {p["name"]: p for p in config.list_agy_profiles(self.current_profile)}
            info = profiles.get(profile)
            if not info or not info.get("logged_in"):
                raise RuntimeError(f"账号 {profile} 尚未登录或凭证未生效")

            target = self._create_manager(profile)
            try:
                await target.start()
            except Exception as exc:
                exc_msg = str(exc) or type(exc).__name__
                logger.warning("Reloading profile %s failed: %s", profile, exc_msg)
                try:
                    await target.stop()
                except Exception:
                    pass
                raise RuntimeError(f"重载账号 {profile} 失败: {exc_msg}") from exc

            self._managers[profile] = target
            await self._notify_profile_ready(profile, target)
            logger.info("Reloaded profile %s (warm=%s)", profile, self._is_warm(target))
            return {
                "ok": True,
                "profile": profile,
                "email": target._current_profile_email(),
                "session_id": target.session_id,
                "warm": self._is_warm(target),
            }


