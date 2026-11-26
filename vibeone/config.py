import atexit
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration & Startup Validation
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("vibeone")

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
CODEX_MODEL_CACHE_DIR = BASE_DIR / ".cache" / "codex_models"
LEGACY_DB_PATH = BASE_DIR / "agy_web.db"
_ENV_DB_PATH = os.environ.get("VIBEONE_DB_PATH", "").strip()
if _ENV_DB_PATH:
    DB_PATH = Path(_ENV_DB_PATH).expanduser()
elif "unittest" in sys.modules or "pytest" in sys.modules:
    _TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="vibeone-test-db-"))
    atexit.register(shutil.rmtree, _TEST_DB_DIR, True)
    DB_PATH = _TEST_DB_DIR / "vibeone.db"
else:
    DB_PATH = BASE_DIR / "vibeone.db"
RESTART_MARKER_FILE = BASE_DIR / ".restart_after_turn"
VIBEONE_LAUNCHD_LABEL = (
    os.environ.get("VIBEONE_LAUNCHD_LABEL", "").strip()
    or os.environ.get("AGY_LAUNCHD_LABEL", "").strip()
    or "local.vibeone"
)
AGY_LAUNCHD_LABEL = VIBEONE_LAUNCHD_LABEL
_deferred_restart_scheduled = False

VIBEONE_WEB_TOKEN = (
    os.environ.get("VIBEONE_WEB_TOKEN", "").strip()
    or os.environ.get("AGY_WEB_TOKEN", "").strip()
)
AGY_WEB_TOKEN = VIBEONE_WEB_TOKEN
if not VIBEONE_WEB_TOKEN:
    raise RuntimeError(
        "Missing required environment variable VIBEONE_WEB_TOKEN (or legacy AGY_WEB_TOKEN). "
        "Set VIBEONE_WEB_TOKEN to a strong secret before starting the server."
    )

VIBEONE_WORKDIR = os.path.abspath(
    os.environ.get("VIBEONE_WORKDIR") or os.environ.get("AGY_WORKDIR") or str(BASE_DIR)
)
AGY_WORKDIR = VIBEONE_WORKDIR
VIBEONE_DEFAULT_MODE = (
    os.environ.get("VIBEONE_DEFAULT_MODE", "").strip()
    or os.environ.get("AGY_DEFAULT_MODE", "").strip()
    or "auto"
)
AGY_DEFAULT_MODE = VIBEONE_DEFAULT_MODE
VIBEONE_VAPID_SUBJECT = (
    os.environ.get("VIBEONE_VAPID_SUBJECT", "").strip()
    or os.environ.get("AGY_VAPID_SUBJECT", "").strip()
    or "mailto:admin@example.com"
)
AGY_VAPID_SUBJECT = VIBEONE_VAPID_SUBJECT
AUTH_COOKIE_NAME = "vibeone_token"
LEGACY_AUTH_COOKIE_NAME = "agy_web_token"
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".bmp"}
MAX_UPLOAD_BYTES = int(
    os.environ.get("VIBEONE_MAX_UPLOAD_BYTES")
    or os.environ.get("AGY_MAX_UPLOAD_BYTES")
    or str(25 * 1024 * 1024)
)


def _resolve_real_home() -> Path:
    raw = os.environ.get("USER_REAL_HOME") or os.path.expanduser("~")
    p = Path(raw).resolve()
    if p.parent.name == "profiles" and p.parent.parent.name == "agy":
        return p.parent.parent.parent.parent
    return p


REAL_HOME = _resolve_real_home()
AGY_PROFILES_DIR = Path(
    os.environ.get("AGY_PROFILES_DIR", str(REAL_HOME / ".config/agy/profiles"))
)
CODEX_PROFILE_NAME = "codex"
CODEX_PROFILES_DIR = REAL_HOME / ".config" / "codex" / "profiles"

from .profile_config import (  # noqa: E402
    _decode_jwt_payload,
    _detect_default_profile,
    _is_valid_profile_name,
    _read_codex_auth_hint,
    _sync_shared_agy_skills_config,
    _unwrap_native_codex_binary,
    ensure_codex_home,
    ensure_profile_home,
    get_codex_account_cache_key,
    get_prewarm_profiles,
    get_profile_token_file,
    is_codex_profile,
    list_agy_profiles,
    read_agy_auth,
    resolve_agy_binary,
    resolve_codex_binary,
    resolve_codex_home,
    resolve_profile_home,
)

DEFAULT_AGY_PROFILE = _detect_default_profile()

_deferred_restart_hook = None


def register_deferred_restart_hook(hook) -> None:
    global _deferred_restart_hook
    _deferred_restart_hook = hook


def trigger_deferred_restart(push_task=None) -> bool:
    if _deferred_restart_hook is not None:
        return bool(_deferred_restart_hook(push_task=push_task))
    return False
