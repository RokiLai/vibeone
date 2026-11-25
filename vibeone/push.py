import asyncio
import base64
import json
import time
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from cryptography.hazmat.primitives import serialization as _crypto_serialization
    from py_vapid import Vapid as _Vapid
    from pywebpush import WebPushException as _WebPushException
    from pywebpush import webpush as _webpush_send

    HAS_WEBPUSH = True
except Exception:
    _webpush_send = None
    _WebPushException = Exception
    _Vapid = None
    _crypto_serialization = None
    HAS_WEBPUSH = False

from . import config
from .store import SessionStore

logger = config.logger

# ---------------------------------------------------------------------------
# Web Push Notification Manager (VAPID / RFC 8291 for iOS 16.4+ PWA & Browsers)
# ---------------------------------------------------------------------------

class WebPushManager:
    def __init__(self, store: SessionStore):
        self.store = store
        self.supported: bool = HAS_WEBPUSH
        self._private_pem: str = ""
        self._vapid: Any = None
        self.public_key: str = ""
        if self.supported:
            try:
                self._ensure_vapid_keys()
            except Exception as exc:
                logger.warning("Failed initializing VAPID keys (Web Push disabled): %s", exc)
                self.supported = False

    def _ensure_vapid_keys(self) -> None:
        saved = self.store.get_meta("vapid_keys")
        if isinstance(saved, dict) and saved.get("private_pem") and saved.get("public_b64"):
            self._private_pem = str(saved["private_pem"])
            self.public_key = str(saved["public_b64"])
            self._vapid = _Vapid.from_pem(self._private_pem.encode("utf-8"))
            return

        vapid = _Vapid()
        vapid.generate_keys()
        priv_pem = vapid.private_pem().decode("utf-8")
        raw_pub = vapid.public_key.public_bytes(
            encoding=_crypto_serialization.Encoding.X962,
            format=_crypto_serialization.PublicFormat.UncompressedPoint,
        )
        pub_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode("ascii")
        self._private_pem = priv_pem
        self._vapid = vapid
        self.public_key = pub_b64
        self.store.set_meta("vapid_keys", {"private_pem": priv_pem, "public_b64": pub_b64})
        logger.info("Generated and persisted new VAPID keypair for Web Push")

    def list_subscriptions(self) -> List[Dict[str, Any]]:
        raw = self.store.get_meta("push_subscriptions", [])
        if not isinstance(raw, list):
            return []
        return [s for s in raw if isinstance(s, dict) and s.get("endpoint") and isinstance(s.get("keys"), dict)]

    def subscribe(self, sub: Dict[str, Any]) -> int:
        endpoint = str(sub.get("endpoint") or "").strip()
        keys = sub.get("keys") or {}
        if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
            raise ValueError("推送订阅参数不完整")
        subs = [s for s in self.list_subscriptions() if s.get("endpoint") != endpoint]
        subs.append(
            {
                "endpoint": endpoint,
                "keys": {"p256dh": str(keys["p256dh"]), "auth": str(keys["auth"])},
                "updated_at": time.time(),
            }
        )
        # Keep recent 20 devices
        subs = subs[-20:]
        self.store.set_meta("push_subscriptions", subs)
        return len(subs)

    def unsubscribe(self, endpoint: str) -> int:
        ep = str(endpoint or "").strip()
        subs = [s for s in self.list_subscriptions() if s.get("endpoint") != ep]
        self.store.set_meta("push_subscriptions", subs)
        return len(subs)

    def _send_one_sync(self, sub: Dict[str, Any], payload_str: str) -> Tuple[bool, bool]:
        """Returns (ok, should_prune_dead_subscription)."""
        try:
            _webpush_send(
                subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                data=payload_str,
                vapid_private_key=self._vapid,
                vapid_claims={"sub": config.VIBEONE_VAPID_SUBJECT},
                headers={"Urgency": "high"},
                ttl=3600,
                timeout=12,
            )
            return True, False
        except _WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            resp_body = getattr(getattr(exc, "response", None), "text", "")
            if status in (404, 410):
                return False, True
            logger.warning("WebPush failed (status=%s): %s %s", status, exc, resp_body[:200])
            return False, False
        except Exception as exc:
            logger.warning("WebPush error: %s", exc)
            return False, False

    async def send_notification(
        self, title: str, body: str, session_id: str = "", tag: str = "", profile: str = ""
    ) -> Dict[str, Any]:
        if not self.supported or not self._private_pem:
            return {"sent": 0, "failed": 0, "total": 0}
        subs = self.list_subscriptions()
        if not subs:
            return {"sent": 0, "failed": 0, "total": 0}
        qs_parts = []
        if profile:
            qs_parts.append(f"profile={profile}")
        if session_id:
            qs_parts.append(f"session={session_id}")
        url = f"/?{'&'.join(qs_parts)}" if qs_parts else "/"
        payload_str = json.dumps(
            {
                "title": title,
                "body": body,
                "session_id": session_id,
                "profile": profile,
                "tag": tag or (f"vibeone-{session_id}" if session_id else "vibeone"),
                "url": url,
            },
            ensure_ascii=False,
        )
        results = await asyncio.gather(
            *(asyncio.to_thread(self._send_one_sync, sub, payload_str) for sub in subs),
            return_exceptions=True,
        )
        sent = 0
        failed = 0
        dead_endpoints: Set[str] = set()
        for sub, outcome in zip(subs, results):
            if isinstance(outcome, Exception):
                failed += 1
                logger.warning("WebPush task error: %s", outcome)
                continue
            ok, dead = outcome
            if ok:
                sent += 1
            else:
                failed += 1
                if dead:
                    dead_endpoints.add(sub["endpoint"])
        if dead_endpoints:
            kept = [s for s in subs if s.get("endpoint") not in dead_endpoints]
            self.store.set_meta("push_subscriptions", kept)
            logger.info("Pruned %d expired Web Push subscription(s)", len(dead_endpoints))
        logger.info(
            "WebPush delivered (profile=%s, sid=%s, sent=%d/%d, failed=%d, pruned=%d)",
            profile or "-",
            session_id[:8] if session_id else "-",
            sent,
            len(subs),
            failed,
            len(dead_endpoints),
        )
        return {"sent": sent, "failed": failed, "total": len(subs)}


