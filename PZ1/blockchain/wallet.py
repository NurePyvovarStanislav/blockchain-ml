"""Ed25519 wallet: private signing key, public key and SHA-256 address."""

import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class Wallet:
    def __init__(self, private_key: Ed25519PrivateKey):
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("Expected an Ed25519 private key")
        self._private_key = private_key

    @classmethod
    def generate(cls) -> "Wallet":
        return cls(Ed25519PrivateKey.generate())

    @property
    def public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def public_key_hex(self) -> str:
        return self.public_key_bytes.hex()

    @property
    def address(self) -> str:
        # PZ1 protocol: exactly SHA-256(raw 32-byte Ed25519 public key).
        return hashlib.sha256(self.public_key_bytes).hexdigest()

    def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(message)

    def save_encrypted(self, path: str | Path, password: str) -> None:
        """Create an encrypted PKCS#8 PEM key file without overwriting a file."""
        if not isinstance(password, str) or len(password) < 12:
            raise ValueError("Use a password of at least 12 characters")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        encrypted_pem = self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(
                password.encode("utf-8")
            ),
        )
        # Avoid accidentally overwriting an existing wallet. On POSIX, restrict
        # permissions at creation; on Windows the password is essential too.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encrypted_pem)

    @classmethod
    def load_encrypted(cls, path: str | Path, password: str) -> "Wallet":
        private_key = serialization.load_pem_private_key(
            Path(path).read_bytes(), password=password.encode("utf-8")
        )
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("File does not contain an Ed25519 private key")
        return cls(private_key)
