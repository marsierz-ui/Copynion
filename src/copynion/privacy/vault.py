"""Key management and encryption for the data that is sensitive at rest.

Design notes
------------
Copynion does *not* encrypt the whole database. Timestamps, application names
and categories stay in plaintext columns so that statistics remain fast SQL
aggregates. Window titles - by far the most revealing field - are sealed
individually with AES-GCM.

Alongside the ciphertext we store a keyed fingerprint (HMAC-SHA256, truncated)
of the redacted title. That is what lets stage 2 count "you opened this same
thing 43 times this week" and lets stage 3 spot repeating sequences, without the
statistics engine ever decrypting anything. The fingerprint is keyed, so a
stolen database cannot be attacked with a dictionary of likely titles.

If the ``cryptography`` package is unavailable the vault **fails closed**: titles
are dropped rather than written in the clear. Set ``allow_unencrypted_titles``
in the config to override that, which the CLI warns about loudly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path

# A broken cryptography install is not merely absent: a mismatched Rust/cffi
# build raises pyo3_runtime.PanicException, which derives from BaseException and
# would otherwise take the whole observer down at import time. The observer must
# survive that, so the guard is deliberately wide. The consequence of landing in
# the except branch is fail-closed (titles are dropped), never plaintext on disk.
try:  # pragma: no cover - import guard is environment-specific
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    CRYPTO_AVAILABLE = True
except BaseException:  # noqa: BLE001 - see comment above
    AESGCM = None  # type: ignore[assignment]

    class InvalidTag(Exception):  # type: ignore[no-redef]
        """Stand-in so ``except InvalidTag`` stays valid without the library."""

    CRYPTO_AVAILABLE = False

KEY_BYTES = 32
NONCE_BYTES = 12
_AAD = b"copynion/title/v1"
_AAD_BLOB = b"copynion/blob/v1"
_KEYRING_SERVICE = "copynion"
_KEYRING_USER = "vault-key"


class VaultError(RuntimeError):
    """Raised when the vault cannot do what was asked of it."""


class Vault:
    """Holds the master key and seals/opens individual values."""

    def __init__(
        self,
        key: bytes | None,
        *,
        allow_unencrypted: bool = False,
        key_location: str = "memory",
    ) -> None:
        if key is not None and len(key) != KEY_BYTES:
            raise VaultError(f"vault key must be {KEY_BYTES} bytes, got {len(key)}")
        self._key = key
        self.allow_unencrypted = allow_unencrypted
        #: Where the key actually lives - "keyring", "file:<path>", "memory" or
        #: "none". Reported verbatim by the CLI: telling someone their key is in
        #: the OS keychain when it silently fell back to a file on disk would be
        #: a lie about where their secret is, so this tracks what really happened.
        self.key_location = key_location

    # -- lifecycle ------------------------------------------------------------

    @classmethod
    def generate_key(cls) -> bytes:
        return secrets.token_bytes(KEY_BYTES)

    @classmethod
    def load(cls, key_path: Path, *, create: bool = False, use_keyring: bool = True,
             allow_unencrypted: bool = False) -> Vault:
        """Load the key from the OS keychain, falling back to a 0600 file."""
        key, location = None, "none"
        if use_keyring:
            key = cls._keyring_get()
            if key is not None:
                location = "keyring"
        if key is None:
            key = cls._file_get(key_path)
            if key is not None:
                location = f"file:{key_path}"
        if key is None and create:
            key = cls.generate_key()
            if use_keyring and cls._keyring_set(key):
                location = "keyring"
            else:
                cls._file_set(key_path, key)
                location = f"file:{key_path}"
        return cls(key, allow_unencrypted=allow_unencrypted, key_location=location)

    @property
    def available(self) -> bool:
        """True when titles can actually be encrypted."""
        return CRYPTO_AVAILABLE and self._key is not None

    @property
    def can_store_titles(self) -> bool:
        return self.available or self.allow_unencrypted

    def status(self) -> dict[str, object]:
        return {
            "crypto_library": CRYPTO_AVAILABLE,
            "key_loaded": self._key is not None,
            "encryption_active": self.available,
            "allow_unencrypted": self.allow_unencrypted,
            "key_location": self.key_location,
        }

    def describe_key_location(self) -> str:
        """Human-readable, and honest about the keyring fallback."""
        if self.key_location == "keyring":
            return "your OS keychain"
        if self.key_location.startswith("file:"):
            return f"{self.key_location[5:]} (no OS keychain available, so a 0600 file is used)"
        if self.key_location == "memory":
            return "memory only (not persisted)"
        return "nowhere - no key exists yet"

    # -- sealing --------------------------------------------------------------

    def seal(self, plaintext: str) -> bytes | None:
        """Encrypt ``plaintext``. Returns ``None`` when storage is not permitted.

        The returned blob is ``version || nonce || ciphertext+tag``. Version 0
        marks an unencrypted fallback so :meth:`open` can read old rows after the
        user installs the crypto extra.
        """
        if not plaintext:
            return None
        if self.available:
            nonce = os.urandom(NONCE_BYTES)
            ct = AESGCM(self._key).encrypt(nonce, plaintext.encode("utf-8"), _AAD)
            return b"\x01" + nonce + ct
        if self.allow_unencrypted:
            return b"\x00" + plaintext.encode("utf-8")
        # Fail closed: no key, no plaintext on disk.
        return None

    def open(self, blob: bytes | None) -> str | None:
        """Decrypt a blob produced by :meth:`seal`."""
        if not blob:
            return None
        version, body = blob[:1], blob[1:]
        if version == b"\x00":
            return body.decode("utf-8", errors="replace")
        if version != b"\x01":
            raise VaultError(f"unknown sealed-value version {version!r}")
        if not self.available:
            raise VaultError("cannot decrypt: vault key unavailable or cryptography not installed")
        nonce, ct = body[:NONCE_BYTES], body[NONCE_BYTES:]
        try:
            return AESGCM(self._key).decrypt(nonce, ct, _AAD).decode("utf-8")
        except InvalidTag as exc:
            raise VaultError("sealed value failed authentication (wrong key or tampering)") from exc

    def seal_blob(self, data: bytes) -> bytes | None:
        """Seal arbitrary bytes - used for compressed input batches.

        A separate AAD from :meth:`seal` gives domain separation, so a sealed
        title can never be swapped in for a sealed input batch or vice versa.
        """
        if not data:
            return None
        if self.available:
            nonce = os.urandom(NONCE_BYTES)
            return b"\x01" + nonce + AESGCM(self._key).encrypt(nonce, data, _AAD_BLOB)
        if self.allow_unencrypted:
            return b"\x00" + data
        return None

    def open_blob(self, blob: bytes | None) -> bytes | None:
        if not blob:
            return None
        version, body = blob[:1], blob[1:]
        if version == b"\x00":
            return body
        if version != b"\x01":
            raise VaultError(f"unknown sealed-blob version {version!r}")
        if not self.available:
            raise VaultError("cannot decrypt: vault key unavailable")
        nonce, ct = body[:NONCE_BYTES], body[NONCE_BYTES:]
        try:
            return AESGCM(self._key).decrypt(nonce, ct, _AAD_BLOB)
        except InvalidTag as exc:
            raise VaultError("sealed blob failed authentication") from exc

    def fingerprint(self, text: str, length: int = 16) -> str | None:
        """Stable keyed hash of ``text`` for grouping without decryption."""
        if not text:
            return None
        if self._key is None:
            # Unkeyed fallback: still useful for grouping within one database,
            # but salted per-process so it cannot be compared across machines.
            digest = hashlib.sha256(_PROCESS_SALT + text.encode("utf-8")).hexdigest()
        else:
            digest = hmac.new(self._key, text.encode("utf-8"), hashlib.sha256).hexdigest()
        return digest[:length]

    # -- key storage backends -------------------------------------------------

    @staticmethod
    def _keyring_get() -> bytes | None:
        try:
            import keyring
        except ImportError:
            return None
        try:
            raw = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        except Exception:  # keyring raises a zoo of backend-specific errors
            return None
        return base64.b64decode(raw) if raw else None

    @staticmethod
    def _keyring_set(key: bytes) -> bool:
        try:
            import keyring
        except ImportError:
            return False
        try:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, base64.b64encode(key).decode())
            return True
        except Exception:
            return False

    @staticmethod
    def _file_get(path: Path) -> bytes | None:
        if not path.exists():
            return None
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise VaultError(
                f"key file {path} is readable by other users; run: chmod 600 {path}"
            )
        return base64.b64decode(path.read_text().strip())

    @staticmethod
    def _file_set(path: Path, key: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create with restrictive permissions from the start - never widen later.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(base64.b64encode(key).decode())


_PROCESS_SALT = secrets.token_bytes(16)
