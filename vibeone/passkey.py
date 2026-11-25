import base64
import hashlib
import hmac
import json
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid base64url string")
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


# ---------------------------------------------------------------------------
# Minimal CBOR Decoder (for WebAuthn attestationObject & COSE_Key)
# ---------------------------------------------------------------------------

def _cbor_read_uint(data: bytes, offset: int, addl: int) -> Tuple[int, int]:
    if addl < 24:
        return addl, offset
    if addl == 24:
        return data[offset], offset + 1
    if addl == 25:
        return int.from_bytes(data[offset:offset + 2], "big"), offset + 2
    if addl == 26:
        return int.from_bytes(data[offset:offset + 4], "big"), offset + 4
    if addl == 27:
        return int.from_bytes(data[offset:offset + 8], "big"), offset + 8
    raise ValueError(f"Unsupported CBOR additional info: {addl}")


def _cbor_decode_item(data: bytes, offset: int) -> Tuple[Any, int]:
    if offset >= len(data):
        raise ValueError("Unexpected end of CBOR data")
    initial = data[offset]
    offset += 1
    major = initial >> 5
    addl = initial & 0x1F

    if major == 0:  # unsigned int
        val, offset = _cbor_read_uint(data, offset, addl)
        return val, offset
    if major == 1:  # negative int
        val, offset = _cbor_read_uint(data, offset, addl)
        return -1 - val, offset
    if major == 2:  # byte string
        length, offset = _cbor_read_uint(data, offset, addl)
        end = offset + length
        if end > len(data):
            raise ValueError("CBOR byte string out of bounds")
        return data[offset:end], end
    if major == 3:  # text string
        length, offset = _cbor_read_uint(data, offset, addl)
        end = offset + length
        if end > len(data):
            raise ValueError("CBOR text string out of bounds")
        return data[offset:end].decode("utf-8"), end
    if major == 4:  # array
        length, offset = _cbor_read_uint(data, offset, addl)
        items = []
        for _ in range(length):
            item, offset = _cbor_decode_item(data, offset)
            items.append(item)
        return items, offset
    if major == 5:  # map
        length, offset = _cbor_read_uint(data, offset, addl)
        mapping = {}
        for _ in range(length):
            k, offset = _cbor_decode_item(data, offset)
            v, offset = _cbor_decode_item(data, offset)
            mapping[k] = v
        return mapping, offset
    if major == 7:  # simple / bool / null
        if addl == 20:
            return False, offset
        if addl == 21:
            return True, offset
        if addl in (22, 23):
            return None, offset
    raise ValueError(f"Unsupported CBOR major={major} addl={addl}")


def cbor_decode(data: bytes) -> Any:
    val, offset = _cbor_decode_item(data, 0)
    if offset != len(data):
        raise ValueError("Trailing bytes in CBOR data")
    return val


# ---------------------------------------------------------------------------
# Origin / RP ID Helpers
# ---------------------------------------------------------------------------

def resolve_rp_and_origin(request: Any) -> Tuple[str, str]:
    """Resolve WebAuthn RP ID (hostname) and expected Origin from the HTTP request."""
    headers = getattr(request, "headers", {}) or {}
    host = (
        headers.get("x-forwarded-host")
        or headers.get("host")
        or getattr(getattr(request, "url", None), "netloc", "")
        or "localhost"
    )
    host = host.split(",")[0].strip()
    rp_id = host.split(":")[0].lower()

    forwarded_proto = headers.get("x-forwarded-proto", "")
    scheme = (
        forwarded_proto.split(",")[0].strip().lower()
        if forwarded_proto
        else (getattr(getattr(request, "url", None), "scheme", "") or "https").lower()
    )
    origin_hdr = headers.get("origin", "").strip()
    if origin_hdr:
        parsed = urlparse(origin_hdr)
        if parsed.hostname and parsed.hostname.lower() == rp_id and parsed.scheme in ("https", "http"):
            return rp_id, f"{parsed.scheme}://{parsed.netloc}"
    return rp_id, f"{scheme}://{host}"


def _verify_client_data(
    client_data_json_b64: str,
    expected_type: str,
    expected_challenge: str,
    expected_origin: str,
) -> bytes:
    raw = b64url_decode(client_data_json_b64)
    data = json.loads(raw.decode("utf-8"))
    if data.get("type") != expected_type:
        raise ValueError(f"Unexpected clientData type: {data.get('type')}")
    if not hmac.compare_digest(str(data.get("challenge", "")), expected_challenge):
        raise ValueError("Challenge mismatch")
    actual_origin = str(data.get("origin", "")).rstrip("/")
    if not hmac.compare_digest(actual_origin, expected_origin.rstrip("/")):
        raise ValueError(f"Origin mismatch: {actual_origin} != {expected_origin}")
    return raw


def _cose_key_to_pem(cose_key: Dict[Any, Any]) -> Tuple[str, int]:
    kty = cose_key.get(1)
    alg = cose_key.get(3)

    if kty == 2 and alg == -7:  # EC2 / ES256 (P-256)
        crv = cose_key.get(-1)
        x = cose_key.get(-2)
        y = cose_key.get(-3)
        if crv != 1 or not isinstance(x, bytes) or not isinstance(y, bytes):
            raise ValueError("Unsupported EC2 COSE key parameters")
        pub = ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"),
            int.from_bytes(y, "big"),
            ec.SECP256R1(),
        ).public_key()
        pem = pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        return pem, -7

    if kty == 3 and alg == -257:  # RSA / RS256
        n = cose_key.get(-1)
        e = cose_key.get(-2)
        if not isinstance(n, bytes) or not isinstance(e, bytes):
            raise ValueError("Unsupported RSA COSE key parameters")
        pub = rsa.RSAPublicNumbers(
            int.from_bytes(e, "big"),
            int.from_bytes(n, "big"),
        ).public_key()
        pem = pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        return pem, -257

    raise ValueError(f"Unsupported COSE key type={kty} alg={alg}")


def verify_registration(
    attestation_object_b64: str,
    client_data_json_b64: str,
    expected_challenge: str,
    expected_rp_id: str,
    expected_origin: str,
) -> Dict[str, Any]:
    """Verify WebAuthn registration response and return credential metadata."""
    _verify_client_data(
        client_data_json_b64,
        expected_type="webauthn.create",
        expected_challenge=expected_challenge,
        expected_origin=expected_origin,
    )

    att_obj = cbor_decode(b64url_decode(attestation_object_b64))
    auth_data = att_obj.get("authData") if isinstance(att_obj, dict) else None
    if not isinstance(auth_data, bytes) or len(auth_data) < 55:
        raise ValueError("Invalid authenticator data in attestationObject")

    expected_rp_hash = hashlib.sha256(expected_rp_id.encode("utf-8")).digest()
    if not hmac.compare_digest(auth_data[:32], expected_rp_hash):
        raise ValueError("RP ID hash mismatch")

    flags = auth_data[32]
    if not (flags & 0x01):  # User Present
        raise ValueError("User present flag not set")
    if not (flags & 0x40):  # Attested Credential Data
        raise ValueError("Attested credential data flag not set")

    sign_count = int.from_bytes(auth_data[33:37], "big")
    cred_id_len = int.from_bytes(auth_data[53:55], "big")
    cred_end = 55 + cred_id_len
    if cred_end >= len(auth_data):
        raise ValueError("Missing COSE public key in attested credential data")

    cred_id = auth_data[55:cred_end]
    cose_key = cbor_decode(auth_data[cred_end:])
    if not isinstance(cose_key, dict):
        raise ValueError("Invalid COSE public key")

    pem, alg = _cose_key_to_pem(cose_key)
    return {
        "credential_id": b64url_encode(cred_id),
        "public_key_pem": pem,
        "alg": alg,
        "sign_count": sign_count,
    }


def verify_authentication(
    authenticator_data_b64: str,
    client_data_json_b64: str,
    signature_b64: str,
    expected_challenge: str,
    expected_rp_id: str,
    expected_origin: str,
    public_key_pem: str,
    alg: int,
    stored_sign_count: int,
) -> int:
    """Verify WebAuthn assertion response and return updated sign_count."""
    client_data_raw = _verify_client_data(
        client_data_json_b64,
        expected_type="webauthn.get",
        expected_challenge=expected_challenge,
        expected_origin=expected_origin,
    )

    auth_data = b64url_decode(authenticator_data_b64)
    if len(auth_data) < 37:
        raise ValueError("Invalid authenticatorData length")

    expected_rp_hash = hashlib.sha256(expected_rp_id.encode("utf-8")).digest()
    if not hmac.compare_digest(auth_data[:32], expected_rp_hash):
        raise ValueError("RP ID hash mismatch")

    flags = auth_data[32]
    if not (flags & 0x01):  # User Present
        raise ValueError("User present flag not set")

    sign_count = int.from_bytes(auth_data[33:37], "big")
    if stored_sign_count > 0 and sign_count > 0 and sign_count <= stored_sign_count:
        raise ValueError("Passkey sign count did not advance")

    sig = b64url_decode(signature_b64)
    signed_payload = auth_data + hashlib.sha256(client_data_raw).digest()
    pub_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))

    if alg == -7 and isinstance(pub_key, ec.EllipticCurvePublicKey):
        pub_key.verify(sig, signed_payload, ec.ECDSA(hashes.SHA256()))
    elif alg == -257 and isinstance(pub_key, rsa.RSAPublicKey):
        pub_key.verify(sig, signed_payload, padding.PKCS1v15(), hashes.SHA256())
    else:
        raise ValueError(f"Unsupported key/algorithm combination: alg={alg}")

    return sign_count
