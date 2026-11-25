import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _detect_default_profile() -> str:
    env_p = os.environ.get("AGY_PROFILE", "").strip()
    if env_p:
        return env_p
    explicit_bin = os.environ.get("AGY_BIN", "").strip()
    if explicit_bin:
        bin_name = Path(explicit_bin).name
        if bin_name and (AGY_PROFILES_DIR / bin_name).is_dir():
            return bin_name
    try:
        discovered = list_agy_profiles()
    except OSError:
        discovered = []
    for p in discovered:
        if p.get("logged_in"):
            return str(p["name"])
    if discovered:
        return str(discovered[0]["name"])
    return "default"


CODEX_PROFILE_NAME = "codex"
CODEX_PROFILES_DIR = REAL_HOME / ".config" / "codex" / "profiles"


def get_prewarm_profiles(
    discovered_profiles: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """Return profile names to asynchronously start on server startup.

    By default (or when set to ``all``), scans all discovered logged-in profiles
    so every available engine starts asynchronously without primary/secondary distinction.
    """
    raw = os.environ.get("VIBEONE_PREWARM_PROFILES")
    if raw is not None:
        cleaned = raw.strip()
        if not cleaned or cleaned.lower() in {"none", "off", "false", "0"}:
            return []
        if cleaned.lower() != "all":
            names = [part.strip() for part in cleaned.split(",") if part.strip()]
        else:
            profiles = list_agy_profiles() if discovered_profiles is None else discovered_profiles
            names = [p["name"] for p in profiles if p.get("logged_in")]
    else:
        profiles = list_agy_profiles() if discovered_profiles is None else discovered_profiles
        names = [p["name"] for p in profiles if p.get("logged_in")]
    seen = set()
    result: List[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def is_codex_profile(profile: Optional[str]) -> bool:
    p = str(profile or "").strip()
    if not p:
        return False
    if p == CODEX_PROFILE_NAME:
        return True
    return _is_valid_profile_name(p) and (CODEX_PROFILES_DIR / p).is_dir()


def _unwrap_native_codex_binary(bin_path: str) -> str:
    """If `bin_path` is the npm JS wrapper (`codex.js`), resolve the native Rust binary directly."""
    try:
        real = Path(bin_path).resolve()
        if real.suffix == ".js":
            pkg_root = real.parent.parent
            for rel in (
                "node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
                "node_modules/@openai/codex-darwin-x64/vendor/x86_64-apple-darwin/bin/codex",
                "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex",
                "node_modules/@openai/codex-linux-arm64/vendor/aarch64-unknown-linux-musl/bin/codex",
                "vendor/aarch64-apple-darwin/bin/codex",
                "vendor/x86_64-apple-darwin/bin/codex",
                "vendor/x86_64-unknown-linux-musl/bin/codex",
                "vendor/aarch64-unknown-linux-musl/bin/codex",
            ):
                cand = pkg_root / rel
                if cand.is_file() and os.access(str(cand), os.X_OK):
                    return str(cand)
    except Exception:
        pass
    return bin_path


def resolve_codex_binary() -> Optional[str]:
    explicit = os.environ.get("CODEX_BIN", "").strip()
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return _unwrap_native_codex_binary(explicit)
    found = shutil.which("codex")
    if found:
        return _unwrap_native_codex_binary(found)
    for cand in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex"):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return _unwrap_native_codex_binary(cand)
    return None


def resolve_codex_home(profile: Optional[str] = None) -> Path:
    p = str(profile or "").strip()
    if p and p != CODEX_PROFILE_NAME:
        if not _is_valid_profile_name(p):
            raise ValueError(f"非法 Codex 账号名称: {p}")
        return CODEX_PROFILES_DIR / p
    explicit = os.environ.get("CODEX_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return REAL_HOME / ".codex"


def ensure_codex_home(profile: Optional[str] = None) -> Path:
    home = resolve_codex_home(profile)
    home.mkdir(parents=True, exist_ok=True)
    default_home = REAL_HOME / ".codex"
    if home != default_home:
        src_skills = default_home / "skills"
        dst_skills = home / "skills"
        if src_skills.exists() and not dst_skills.exists() and not dst_skills.is_symlink():
            try:
                dst_skills.symlink_to(src_skills)
            except OSError:
                pass
    return home


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2 or not parts[1]:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_codex_account_cache_key(codex_home: Optional[Path] = None) -> str:
    """Return a stable, non-sensitive per-account cache key for Codex model catalogs."""
    home = Path(codex_home) if codex_home is not None else resolve_codex_home()
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return hashlib.sha256(f"apikey:{env_key}".encode("utf-8")).hexdigest()[:16]

    auth_file = home / "auth.json"
    try:
        if auth_file.is_file():
            data = json.loads(auth_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                file_key = str(data.get("OPENAI_API_KEY") or "").strip()
                if file_key:
                    return hashlib.sha256(f"apikey:{file_key}".encode("utf-8")).hexdigest()[:16]
                tokens = data.get("tokens") or {}
                if isinstance(tokens, dict):
                    acct_id = str(tokens.get("account_id") or "").strip()
                    if acct_id:
                        return hashlib.sha256(f"acct:{acct_id}".encode("utf-8")).hexdigest()[:16]
                    id_token = str(tokens.get("id_token") or "").strip()
                    if id_token:
                        claims = _decode_jwt_payload(id_token)
                        ident = str(claims.get("email") or claims.get("sub") or "").strip()
                        if ident:
                            return hashlib.sha256(f"jwt:{ident}".encode("utf-8")).hexdigest()[:16]
                    acc_tok = str(tokens.get("access_token") or "").strip()
                    if acc_tok:
                        return hashlib.sha256(f"tok:{acc_tok[:64]}".encode("utf-8")).hexdigest()[:16]
    except Exception:
        pass

    cache_file = home / "models_cache.json"
    try:
        if cache_file.is_file():
            cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(cache_data, dict):
                ident = str(cache_data.get("identity") or "").strip()
                if ident:
                    return hashlib.sha256(f"ident:{ident}".encode("utf-8")).hexdigest()[:16]
    except Exception:
        pass

    return "default"


def _read_codex_auth_hint(profile: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    p = str(profile or "").strip()
    is_default = not p or p == CODEX_PROFILE_NAME
    if is_default and os.environ.get("OPENAI_API_KEY", "").strip():
        return True, "API Key"
    auth_file = resolve_codex_home(profile) / "auth.json"
    try:
        if auth_file.is_file():
            data = json.loads(auth_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if data.get("OPENAI_API_KEY"):
                    return True, "API Key"
                tokens = data.get("tokens") or {}
                if isinstance(tokens, dict) and (tokens.get("access_token") or tokens.get("id_token")):
                    email: Optional[str] = None
                    id_token = str(tokens.get("id_token") or "").strip()
                    if id_token:
                        claims = _decode_jwt_payload(id_token)
                        email = str(claims.get("email") or "").strip() or None
                    return True, email
    except Exception:
        pass
    return (bool(resolve_codex_binary()) if is_default else False), None


def _is_valid_profile_name(name: str) -> bool:
    return bool(name) and all(c.isalnum() or c in ("_", "-") for c in name)


def resolve_profile_home(profile: str) -> Path:
    if is_codex_profile(profile):
        return REAL_HOME
    if profile == "default":
        return REAL_HOME
    if profile == "agy" and not (AGY_PROFILES_DIR / profile).is_dir():
        return REAL_HOME
    if not _is_valid_profile_name(profile):
        raise ValueError(f"非法账号名称: {profile}")
    p = AGY_PROFILES_DIR / profile
    if not p.is_dir():
        raise ValueError(f"账号配置目录不存在: {profile}")
    return p


def ensure_profile_home(profile: str) -> Path:
    home = resolve_profile_home(profile)
    (home / ".gemini").mkdir(parents=True, exist_ok=True)
    if home != REAL_HOME:
        for dot in (".ssh", ".gitconfig", ".zshrc", ".bashrc", ".local"):
            src = REAL_HOME / dot
            dst = home / dot
            if src.exists() and not dst.exists() and not dst.is_symlink():
                try:
                    dst.symlink_to(src)
                except OSError:
                    pass
    return home


def get_profile_token_file(profile: str) -> Path:
    return resolve_profile_home(profile) / ".gemini/antigravity-cli/antigravity-oauth-token"


def read_agy_auth(profile: str) -> Dict[str, Any]:
    """Read existing credentials without copying, modifying, or logging secrets."""
    home = resolve_profile_home(profile)
    tf = get_profile_token_file(profile)
    try:
        if tf.is_file():
            data = json.loads(tf.read_text(encoding="utf-8"))
        elif sys.platform == "darwin" and home == REAL_HOME and home == Path.home().resolve():
            result = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                capture_output=True, text=True, timeout=3, check=True,
            )
            raw = result.stdout.strip()
            prefix = "go-keyring-base64:"
            if raw.startswith(prefix):
                raw = base64.b64decode(raw[len(prefix):], validate=True).decode("utf-8")
            data = json.loads(raw)
        else:
            return {}
        return data if isinstance(data, dict) and isinstance(data.get("token"), dict) else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def list_agy_profiles(current_profile: Optional[str] = None) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    try:
        agy_entries = sorted(AGY_PROFILES_DIR.iterdir(), key=lambda x: x.name) if AGY_PROFILES_DIR.is_dir() else []
    except OSError:
        agy_entries = []
    for entry in agy_entries:
            if not entry.is_dir() or not _is_valid_profile_name(entry.name):
                continue
            tf = entry / ".gemini/antigravity-cli/antigravity-oauth-token"
            email: Optional[str] = None
            logged_in = False
            if tf.is_file():
                try:
                    d = json.loads(tf.read_text(encoding="utf-8"))
                    logged_in = bool((d.get("token") or {}).get("access_token"))
                    email = d.get("id_token_email") or None
                except Exception:
                    pass
            profiles.append(
                {
                    "name": entry.name,
                    "backend": "agy",
                    "email": email,
                    "logged_in": logged_in,
                    "is_current": entry.name == current_profile,
                }
            )
    if not any(p["name"] == "agy" for p in profiles):
        auth = read_agy_auth("agy")
        if (auth.get("token") or {}).get("access_token"):
            profiles.append({
                "name": "agy",
                "backend": "agy",
                "email": auth.get("id_token_email") or None,
                "logged_in": True,
                "is_current": current_profile in ("agy", "default"),
            })
    if resolve_codex_binary() or current_profile == CODEX_PROFILE_NAME:
        codex_logged, codex_email = _read_codex_auth_hint(CODEX_PROFILE_NAME)
        profiles.append(
            {
                "name": CODEX_PROFILE_NAME,
                "backend": "codex",
                "email": codex_email,
                "logged_in": codex_logged,
                "is_current": current_profile == CODEX_PROFILE_NAME,
            }
        )
    try:
        codex_entries = sorted(CODEX_PROFILES_DIR.iterdir(), key=lambda x: x.name) if CODEX_PROFILES_DIR.is_dir() else []
    except OSError:
        codex_entries = []
    for entry in codex_entries:
            if not entry.is_dir() or not _is_valid_profile_name(entry.name) or entry.name == CODEX_PROFILE_NAME:
                continue
            c_logged, c_email = _read_codex_auth_hint(entry.name)
            profiles.append(
                {
                    "name": entry.name,
                    "backend": "codex",
                    "email": c_email,
                    "logged_in": c_logged,
                    "is_current": entry.name == current_profile,
                }
            )
    return profiles


DEFAULT_AGY_PROFILE = _detect_default_profile()


def resolve_agy_binary(for_profile: Optional[str] = None) -> str:
    if for_profile:
        real_explicit = os.environ.get("AGY_REAL_BIN", "").strip()
        if real_explicit and os.path.isfile(real_explicit) and os.access(real_explicit, os.X_OK):
            return real_explicit
        real_local = str(REAL_HOME / ".local/bin/agy")
        if os.path.isfile(real_local) and os.access(real_local, os.X_OK):
            return real_local
        found = shutil.which("agy")
        if found:
            return found
    explicit = os.environ.get("AGY_BIN", "").strip()
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    found = shutil.which("agy")
    if found:
        return found
    fallback = str(REAL_HOME / ".local/bin/agy")
    if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
        return fallback
    raise RuntimeError(
        "Cannot locate `agy` binary. Set AGY_BIN or add ~/.local/bin to PATH."
    )




_deferred_restart_hook = None


def register_deferred_restart_hook(hook) -> None:
    global _deferred_restart_hook
    _deferred_restart_hook = hook


def trigger_deferred_restart(push_task=None) -> bool:
    if _deferred_restart_hook is not None:
        return bool(_deferred_restart_hook(push_task=push_task))
    return False
