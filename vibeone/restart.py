"""Deferred macOS LaunchAgent restart orchestration."""

import asyncio
import os
import subprocess
import sys
from typing import Any, Callable, Optional

from . import config

logger = config.logger
_deferred_restart_scheduled = False


def _spawn_launchd_restart(delay_sec: float = 0.5) -> None:
    """Spawn a detached helper via posix_spawn to kickstart the macOS LaunchAgent."""
    if sys.platform != "darwin" or not os.path.isfile("/bin/launchctl"):
        logger.warning("Deferred LaunchAgent restart is only supported on macOS with /bin/launchctl")
        return
    target_uid = str(os.getuid())
    safe_delay = f"{max(0.1, float(delay_sec)):.2f}"
    subprocess.Popen(
        [
            "/bin/sh",
            "-c",
            'sleep "$1"; exec /bin/launchctl kickstart -k "gui/$2/$3"',
            "--",
            safe_delay,
            target_uid,
            config.VIBEONE_LAUNCHD_LABEL,
        ],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


async def _execute_deferred_restart(
    get_manager: Callable[[], Any],
    push_task: Optional[asyncio.Task] = None,
) -> None:
    global _deferred_restart_scheduled
    try:
        if push_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(push_task), timeout=8.0)
            except Exception as exc:
                logger.warning("Deferred restart waited for push task with error: %s", exc)
        try:
            await get_manager().active.broadcast(
                {
                    "type": "toast",
                    "data": {
                        "level": "info",
                        "message": "服务正在自动重启以应用最新更新...",
                    },
                }
            )
        except Exception:
            pass
        await asyncio.sleep(1.0)
        logger.info(
            "Executing deferred LaunchAgent restart for gui/%d/%s",
            os.getuid(),
            config.VIBEONE_LAUNCHD_LABEL,
        )
        _spawn_launchd_restart(delay_sec=0.3)
    except Exception as exc:
        _deferred_restart_scheduled = False
        logger.error("Deferred restart failed: %s", exc)


def make_deferred_restart_trigger(get_manager: Callable[[], Any]):
    def maybe_trigger_deferred_restart(push_task: Optional[asyncio.Task] = None) -> bool:
        """If .restart_after_turn exists and all turns are idle, restart after push + WS flush."""
        global _deferred_restart_scheduled
        if _deferred_restart_scheduled:
            return False
        if not config.RESTART_MARKER_FILE.exists():
            return False
        if get_manager().any_busy():
            logger.info("Deferred restart marker present, waiting for other busy sessions to finish")
            return False
        try:
            config.RESTART_MARKER_FILE.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Failed to remove restart marker %s: %s", config.RESTART_MARKER_FILE, exc)
        _deferred_restart_scheduled = True
        asyncio.create_task(_execute_deferred_restart(get_manager, push_task=push_task))
        return True

    return maybe_trigger_deferred_restart
