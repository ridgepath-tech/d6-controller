"""Small localhost session-authentication store for the D6 configurator."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any


class AuthError(ValueError):
    """Raised for invalid setup, login, or password-change requests."""


class AuthStore:
    """Persist only an admin password verifier and keep sessions in memory."""

    SCRYPT_N = 2**14
    SCRYPT_R = 8
    SCRYPT_P = 1
    KEY_LENGTH = 64
    SESSION_TTL = 8 * 60 * 60
    FAILURE_WINDOW = 60.0
    FAILURE_LIMIT = 5

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.sessions: dict[str, float] = {}
        self.failures: dict[str, list[float]] = {}

    @property
    def configured(self) -> bool:
        with self.lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return False
            return bool(payload.get("algorithm") == "scrypt" and payload.get("salt") and payload.get("hash"))

    @staticmethod
    def validate_password(password: str) -> None:
        if not isinstance(password, str) or len(password) < 10:
            raise AuthError("Admin password must be at least 10 characters long.")
        if password.strip() != password or any(ord(character) < 32 for character in password):
            raise AuthError("Admin password cannot begin or end with spaces or contain control characters.")

    def _derive(self, password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self.SCRYPT_N,
            r=self.SCRYPT_R,
            p=self.SCRYPT_P,
            dklen=self.KEY_LENGTH,
        )

    def _write_verifier(self, password: str) -> None:
        salt = secrets.token_bytes(16)
        digest = self._derive(password, salt)
        payload = {
            "algorithm": "scrypt",
            "n": self.SCRYPT_N,
            "r": self.SCRYPT_R,
            "p": self.SCRYPT_P,
            "salt": base64.b64encode(salt).decode("ascii"),
            "hash": base64.b64encode(digest).decode("ascii"),
            "updated_at": int(time.time()),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def setup(self, password: str, confirmation: str) -> str:
        self.validate_password(password)
        if password != confirmation:
            raise AuthError("The password confirmation does not match.")
        with self.lock:
            if self.configured:
                raise AuthError("Admin setup has already been completed.")
            self._write_verifier(password)
            return self._new_session()

    def _new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = time.time() + self.SESSION_TTL
        return token

    def _too_many_failures(self, client: str) -> bool:
        now = time.time()
        recent = [stamp for stamp in self.failures.get(client, []) if now - stamp < self.FAILURE_WINDOW]
        self.failures[client] = recent
        return len(recent) >= self.FAILURE_LIMIT

    def login(self, password: str, client: str = "localhost") -> str:
        with self.lock:
            if self._too_many_failures(client):
                raise AuthError("Too many failed attempts. Try again in about a minute.")
            if not self._verify(password):
                self.failures.setdefault(client, []).append(time.time())
                raise AuthError("Incorrect admin password.")
            self.failures.pop(client, None)
            return self._new_session()

    def _verify(self, password: str) -> bool:
        if not self.configured:
            return False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            salt = base64.b64decode(payload["salt"], validate=True)
            expected = base64.b64decode(payload["hash"], validate=True)
            actual = self._derive(password, salt)
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return False
        return hmac.compare_digest(actual, expected)

    def session_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self.lock:
            expires = self.sessions.get(token)
            if expires is None:
                return False
            if expires <= time.time():
                self.sessions.pop(token, None)
                return False
            return True

    def logout(self, token: str | None) -> None:
        if token:
            with self.lock:
                self.sessions.pop(token, None)

    def change_password(self, token: str, password: str, confirmation: str) -> str:
        self.validate_password(password)
        if password != confirmation:
            raise AuthError("The password confirmation does not match.")
        with self.lock:
            if not self.session_valid(token):
                raise AuthError("Authentication required.")
            self._write_verifier(password)
            self.sessions.clear()
            return self._new_session()

    def status(self, token: str | None) -> dict[str, Any]:
        return {"setup_required": not self.configured, "authenticated": self.session_valid(token)}
