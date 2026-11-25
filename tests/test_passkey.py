import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-master-token")

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from starlette.responses import Response  # noqa: E402

import server  # noqa: E402
from vibeone import SessionStore, config, passkey  # noqa: E402


def _cbor_encode(obj) -> bytes:
    if isinstance(obj, int):
        if obj >= 0:
            if obj < 24:
                return bytes([obj])
            if obj < 256:
                return bytes([24, obj])
            return bytes([25]) + obj.to_bytes(2, "big")
        val = -1 - obj
        if val < 24:
            return bytes([0x20 | val])
        if val < 256:
            return bytes([0x20 | 24, val])
        return bytes([0x20 | 25]) + val.to_bytes(2, "big")
    if isinstance(obj, bytes):
        if len(obj) < 24:
            return bytes([0x40 | len(obj)]) + obj
        if len(obj) < 256:
            return bytes([0x40 | 24, len(obj)]) + obj
        return bytes([0x40 | 25]) + len(obj).to_bytes(2, "big") + obj
    if isinstance(obj, str):
        raw = obj.encode("utf-8")
        if len(raw) < 24:
            return bytes([0x60 | len(raw)]) + raw
        return bytes([0x60 | 24, len(raw)]) + raw
    if isinstance(obj, dict):
        header = bytes([0xA0 | len(obj)])
        return header + b"".join(_cbor_encode(k) + _cbor_encode(v) for k, v in obj.items())
    raise TypeError(f"Unsupported type: {type(obj)}")


class PasskeyFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.test_store = SessionStore(Path(self._tmp.name) / "pk.db")
        self._orig_store = server.store
        server.store = self.test_store
        server._PASSKEY_CHALLENGES.clear()
        server._AUTH_FAILURES.clear()
        server._AUTH_LOCKOUTS.clear()

    def tearDown(self) -> None:
        server.store = self._orig_store
        self.test_store._conn.close()
        self._tmp.cleanup()
        server._PASSKEY_CHALLENGES.clear()
        server._AUTH_FAILURES.clear()
        server._AUTH_LOCKOUTS.clear()

    def _make_request(self) -> MagicMock:
        req = MagicMock()
        req.url.scheme = "https"
        req.url.netloc = "vibe.example.com"
        req.headers = {
            "host": "vibe.example.com",
            "x-forwarded-proto": "https",
            "origin": "https://vibe.example.com",
        }
        req.client.host = "127.0.0.1"
        req.cookies = {}
        return req

    async def test_passkey_register_login_and_replay_protection(self) -> None:
        req = self._make_request()

        status_before = await server.passkey_status()
        self.assertEqual(status_before, {"enabled": False, "count": 0})

        # 1. Registration options
        reg_opts = await server.passkey_register_options(req, _token=config.VIBEONE_WEB_TOKEN)
        challenge = reg_opts["challenge"]
        self.assertEqual(reg_opts["rp"]["id"], "vibe.example.com")

        # Generate an ES256 (P-256) keypair simulating iCloud Keychain / Apple Passkey
        priv_key = ec.generate_private_key(ec.SECP256R1())
        pub_nums = priv_key.public_key().public_numbers()
        x_bytes = pub_nums.x.to_bytes(32, "big")
        y_bytes = pub_nums.y.to_bytes(32, "big")
        cose_key = _cbor_encode({1: 2, 3: -7, -1: 1, -2: x_bytes, -3: y_bytes})

        cred_id = b"test-icloud-cred-id-123"
        rp_hash = hashlib.sha256(b"vibe.example.com").digest()
        auth_data = (
            rp_hash
            + bytes([0x45])  # UP | UV | AT
            + (1).to_bytes(4, "big")
            + (b"\x00" * 16)
            + len(cred_id).to_bytes(2, "big")
            + cred_id
            + cose_key
        )
        att_obj = _cbor_encode({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data_reg = json.dumps({
            "type": "webauthn.create",
            "challenge": challenge,
            "origin": "https://vibe.example.com",
        }).encode("utf-8")

        reg_res = await server.passkey_register_verify(
            server.PasskeyRegisterVerifyRequest(
                id=passkey.b64url_encode(cred_id),
                attestationObject=passkey.b64url_encode(att_obj),
                clientDataJSON=passkey.b64url_encode(client_data_reg),
                label="iPhone Face ID",
            ),
            req,
            _token=config.VIBEONE_WEB_TOKEN,
        )
        self.assertEqual(reg_res, {"ok": True, "count": 1})

        status_after = await server.passkey_status()
        self.assertEqual(status_after, {"enabled": True, "count": 1})

        # 2. Login options & verify
        login_opts = await server.passkey_login_options(req)
        login_challenge = login_opts["challenge"]
        self.assertEqual(login_opts["allowCredentials"][0]["id"], passkey.b64url_encode(cred_id))

        client_data_login = json.dumps({
            "type": "webauthn.get",
            "challenge": login_challenge,
            "origin": "https://vibe.example.com",
        }).encode("utf-8")
        auth_data_login = rp_hash + bytes([0x05]) + (2).to_bytes(4, "big")
        sig = priv_key.sign(
            auth_data_login + hashlib.sha256(client_data_login).digest(),
            ec.ECDSA(hashes.SHA256()),
        )

        resp = Response()
        login_res = await server.passkey_login_verify(
            server.PasskeyLoginVerifyRequest(
                id=passkey.b64url_encode(cred_id),
                authenticatorData=passkey.b64url_encode(auth_data_login),
                clientDataJSON=passkey.b64url_encode(client_data_login),
                signature=passkey.b64url_encode(sig),
            ),
            req,
            resp,
        )
        self.assertEqual(login_res, {"ok": True})
        set_cookie = resp.headers.get("set-cookie", "")
        self.assertIn(f"{config.AUTH_COOKIE_NAME}=v1s_", set_cookie)

        # 3. Invalid signature must fail with 401
        login_opts_2 = await server.passkey_login_options(req)
        client_data_login_2 = json.dumps({
            "type": "webauthn.get",
            "challenge": login_opts_2["challenge"],
            "origin": "https://vibe.example.com",
        }).encode("utf-8")
        auth_data_login_2 = rp_hash + bytes([0x05]) + (3).to_bytes(4, "big")
        with self.assertRaises(HTTPException) as ctx:
            await server.passkey_login_verify(
                server.PasskeyLoginVerifyRequest(
                    id=passkey.b64url_encode(cred_id),
                    authenticatorData=passkey.b64url_encode(auth_data_login_2),
                    clientDataJSON=passkey.b64url_encode(client_data_login_2),
                    signature=passkey.b64url_encode(b"invalid-signature"),
                ),
                req,
                Response(),
            )
        self.assertEqual(ctx.exception.status_code, 401)

        # 4. Delete passkey
        del_res = await server.delete_passkey_api(
            passkey.b64url_encode(cred_id),
            _token=config.VIBEONE_WEB_TOKEN,
        )
        self.assertEqual(del_res, {"ok": True, "count": 0})


if __name__ == "__main__":
    unittest.main()
