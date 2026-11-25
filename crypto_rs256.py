"""
AutoPiE 商業授權原型 — 純 Python RS256 (RSA-2048 + SHA-256) JWT 簽章模組
不依賴第三方套件，Windows 本機與 Linux 雲端容器（Docker / Render / AWS）皆可直接執行。
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
from pathlib import Path
from typing import Any

# RFC 8017 SHA-256 DigestInfo DER 前綴
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex(
    "3031300d060960864801650304020105000420"
)

_SMALL_PRIMES = [
    3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
    73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151,
    157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229, 233,
    239, 241, 251, 257, 263, 269, 271, 277, 281, 283, 293, 307, 311, 313, 317,
]


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    pad = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + pad)


def int_to_bytes(val: int, length: int | None = None) -> bytes:
    if length is None:
        length = max(1, (val.bit_length() + 7) // 8)
    return val.to_bytes(length, "big")


def _is_probable_prime(n: int, rounds: int = 32) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n == p:
            return True
        if n % p == 0:
            return False
    # 寫成 n - 1 = 2^r * d
    r = 0
    d = n - 1
    while d % 2 == 0:
        r += 1
        d //= 2
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _generate_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits)
        # 確保最高兩位與最低位為 1（標準 RSA 質數範圍）
        candidate |= (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(candidate):
            return candidate


class RsaKeyPair:
    """RSA-2048 簽章金鑰對（模擬 AWS KMS 非對稱簽章）。"""

    def __init__(self, kid: str, n: int, e: int, d: int):
        self.kid = kid
        self.n = n
        self.e = e
        self.d = d
        self.key_bytes = (n.bit_length() + 7) // 8

    @classmethod
    def generate(cls, kid: str = "autopie-proto-2026-01", bits: int = 2048) -> "RsaKeyPair":
        e = 65537
        half = bits // 2
        while True:
            p = _generate_prime(half)
            q = _generate_prime(bits - half)
            if p == q:
                continue
            phi = (p - 1) * (q - 1)
            if math.gcd(e, phi) == 1:
                d = pow(e, -1, phi)
                return cls(kid=kid, n=p * q, e=e, d=d)

    @classmethod
    def load_or_create(cls, path: Path, kid: str = "autopie-proto-2026-01") -> "RsaKeyPair":
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                kid=data["kid"],
                n=int.from_bytes(b64url_decode(data["n"]), "big"),
                e=int.from_bytes(b64url_decode(data["e"]), "big"),
                d=int.from_bytes(b64url_decode(data["d"]), "big"),
            )
        kp = cls.generate(kid=kid)
        payload = {
            "kid": kp.kid,
            "n": b64url_encode(int_to_bytes(kp.n)),
            "e": b64url_encode(int_to_bytes(kp.e)),
            "d": b64url_encode(int_to_bytes(kp.d)),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return kp

    def public_jwk(self) -> dict[str, str]:
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": b64url_encode(int_to_bytes(self.n)),
            "e": b64url_encode(int_to_bytes(self.e)),
        }

    def sign_pkcs1v15_sha256(self, message: bytes) -> bytes:
        digest = hashlib.sha256(message).digest()
        t = _SHA256_DIGEST_INFO_PREFIX + digest
        ps_len = self.key_bytes - len(t) - 3
        if ps_len < 8:
            raise ValueError("RSA key too small for SHA-256 signing")
        em = b"\x00\x01" + (b"\xff" * ps_len) + b"\x00" + t
        m = int.from_bytes(em, "big")
        s = pow(m, self.d, self.n)
        return int_to_bytes(s, self.key_bytes)

    def verify_pkcs1v15_sha256(self, message: bytes, signature: bytes) -> bool:
        if len(signature) != self.key_bytes:
            return False
        s = int.from_bytes(signature, "big")
        if s >= self.n:
            return False
        m = pow(s, self.e, self.n)
        em = int_to_bytes(m, self.key_bytes)
        digest = hashlib.sha256(message).digest()
        t = _SHA256_DIGEST_INFO_PREFIX + digest
        ps_len = self.key_bytes - len(t) - 3
        expected = b"\x00\x01" + (b"\xff" * ps_len) + b"\x00" + t
        return secrets.compare_digest(em, expected)

    def sign_jwt(self, payload: dict[str, Any]) -> str:
        header = {"alg": "RS256", "typ": "JWT", "kid": self.kid}
        h_b64 = b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        p_b64 = b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        sig = self.sign_pkcs1v15_sha256(signing_input)
        return f"{h_b64}.{p_b64}.{b64url_encode(sig)}"

    def verify_jwt(self, token: str) -> dict[str, Any] | None:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        h_b64, p_b64, s_b64 = parts
        try:
            header = json.loads(b64url_decode(h_b64).decode("utf-8"))
            if header.get("alg") != "RS256" or header.get("kid") != self.kid:
                return None
            sig = b64url_decode(s_b64)
            signing_input = f"{h_b64}.{p_b64}".encode("ascii")
            if not self.verify_pkcs1v15_sha256(signing_input, sig):
                return None
            return json.loads(b64url_decode(p_b64).decode("utf-8"))
        except Exception:
            return None
