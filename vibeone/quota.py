import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config

logger = config.logger

AGY_OAUTH_TOKEN_FILE = os.environ.get(
    "AGY_OAUTH_TOKEN_FILE",
    str(Path.home() / ".config/agy/profiles/agy2/.gemini/antigravity-cli/antigravity-oauth-token"),
)
AGY_QUOTA_ENDPOINTS = [
    h.strip()
    for h in os.environ.get(
        "AGY_QUOTA_ENDPOINTS", "https://daily-cloudcode-pa.googleapis.com,https://cloudcode-pa.googleapis.com"
    ).split(",")
    if h.strip()
]
_quota_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_QUOTA_CACHE_TTL = 30.0


def _read_access_token(profile: Optional[str] = None) -> str:
    """Read OAuth access token from the profile's existing storage. Never logged."""
    explicit = os.environ.get("AGY_OAUTH_TOKEN_FILE", "").strip()
    if explicit and profile is None:
        with open(explicit, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = config.read_agy_auth(profile or config.DEFAULT_AGY_PROFILE)
    tok = (data.get("token") or {}).get("access_token") or ""
    if not tok:
        raise RuntimeError("凭证文件中没有 access_token")
    return tok


def parse_quota_response(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Group models sharing the same quota pool (same remainingFraction + resetTime).

    Proto3 omits zero values, so a quotaInfo with resetTime but no remainingFraction means 0 remaining.
    """
    models = payload.get("models") or {}
    items = models.items() if isinstance(models, dict) else [(m.get("id", ""), m) for m in models if isinstance(m, dict)]
    groups: Dict[Tuple[float, str], Dict[str, Any]] = {}
    for mid, m in items:
        if not isinstance(m, dict):
            continue
        name = m.get("displayName")
        qi = m.get("quotaInfo")
        if not name or not isinstance(qi, dict):
            continue
        reset = str(qi.get("resetTime") or "")
        if "remainingFraction" in qi:
            frac = float(qi.get("remainingFraction") or 0.0)
        elif reset:
            frac = 0.0
        else:
            continue
        frac = max(0.0, min(1.0, frac))
        key = (round(frac, 6), reset)
        g = groups.setdefault(key, {"remaining": frac, "reset_time": reset, "models": []})
        if name not in g["models"]:
            g["models"].append(name)
    out = sorted(groups.values(), key=lambda g: (g["remaining"], g["reset_time"]))
    for g in out:
        g["models"].sort()
    return out


def parse_quota_summary(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse retrieveUserQuotaSummary: groups with separate weekly / 5h buckets."""
    out: List[Dict[str, Any]] = []
    for g in payload.get("groups") or []:
        if not isinstance(g, dict):
            continue
        desc = str(g.get("description") or "")
        members = []
        if ":" in desc:
            members = [x.strip() for x in desc.split(":", 1)[1].split(",") if x.strip()]
        buckets = []
        for b in g.get("buckets") or []:
            if not isinstance(b, dict):
                continue
            buckets.append(
                {
                    "window": str(b.get("window") or b.get("bucketId") or ""),
                    "label": str(b.get("displayName") or ""),
                    "remaining": max(0.0, min(1.0, float(b.get("remainingFraction") or 0.0))),
                    "reset_time": str(b.get("resetTime") or ""),
                }
            )
        order = {"5h": 0, "weekly": 1}
        buckets.sort(key=lambda b: order.get(b["window"], 9))
        out.append({"name": str(g.get("displayName") or ""), "members": members, "buckets": buckets})
    return out


def _post_json(base: str, method: str, token: str) -> Dict[str, Any]:
    import urllib.request

    req = urllib.request.Request(
        f"{base}/v1internal:{method}",
        data=b"{}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "antigravity"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_quota_sync(profile: Optional[str] = None) -> Dict[str, Any]:
    import urllib.error

    target_profile = profile or config.DEFAULT_AGY_PROFILE
    token = _read_access_token(target_profile)
    last_err = ""
    for base in AGY_QUOTA_ENDPOINTS:
        try:
            models_payload = _post_json(base, "fetchAvailableModels", token)
        except urllib.error.HTTPError as exc:
            last_err = f"{base} HTTP {exc.code}"
            continue
        except Exception as exc:
            last_err = f"{base} {type(exc).__name__}"
            continue
        summary: List[Dict[str, Any]] = []
        try:
            summary = parse_quota_summary(_post_json(base, "retrieveUserQuotaSummary", token))
        except Exception as exc:
            logger.warning("retrieveUserQuotaSummary failed: %s", type(exc).__name__)
        covered = [m for g in summary for m in g["members"]]
        groups = []
        for g in parse_quota_response(models_payload):
            rest = [n for n in g["models"] if not any(n.startswith(c) for c in covered)]
            if rest:
                groups.append({**g, "models": rest})
        return {"profile": target_profile, "endpoint": base, "summary": summary, "groups": groups}
    raise RuntimeError(last_err or "无可用接口")




def clear_quota_cache() -> None:
    _quota_cache["ts"] = 0.0
    _quota_cache["data"] = None
