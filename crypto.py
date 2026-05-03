"""
crypto.py — Fernet encryption helper for sensitive settings (e.g. SMTP password).
The encryption key is stored in data/.key which is excluded from git via .gitignore.
"""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_KEY_PATH = Path(__file__).parent / "data" / ".key"


def _load_or_create_key() -> bytes:
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _KEY_PATH.exists():
        return _KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    _KEY_PATH.write_bytes(key)
    return key


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string. Returns empty string if input is empty."""
    if not plaintext:
        return ""
    return Fernet(_load_or_create_key()).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Decrypt a Fernet token string. Returns empty string on any failure."""
    if not token:
        return ""
    try:
        return Fernet(_load_or_create_key()).decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return ""
