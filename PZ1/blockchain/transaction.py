"""Signed transaction payloads, serialization and cryptographic validation.

Important: verify_signature() only validates authorization and format. The
mempool in step 2 must separately validate balances, nonces and duplicates.
"""

from dataclasses import dataclass
import hashlib
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .config import NETWORK_ID, TRANSACTION_VERSION
from .serialization import canonical_bytes
from .wallet import Wallet


_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX128 = re.compile(r"[0-9a-f]{128}\Z")


@dataclass(frozen=True)
class Transaction:
    version: int
    network: str
    sender: str
    recipient: str
    amount: int
    fee: int
    nonce: int
    public_key: str
    signature: str

    def unsigned_payload(self) -> dict:
        """Exact fields covered by the signature; signature excluded."""
        return {
            "version": self.version,
            "network": self.network,
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": self.amount,
            "fee": self.fee,
            "nonce": self.nonce,
            "public_key": self.public_key,
        }

    def signing_bytes(self) -> bytes:
        return canonical_bytes(self.unsigned_payload())

    def to_dict(self) -> dict:
        return {**self.unsigned_payload(), "signature": self.signature}

    @property
    def txid(self) -> str:
        # A stable identifier of the full, *signed* transaction.
        return hashlib.sha256(canonical_bytes(self.to_dict())).hexdigest()

    def verify_signature(self) -> bool:
        """Verify exact schema, key/address binding and Ed25519 signature.

        Does NOT check available funds, mempool conflicts or expected nonce.
        """
        if type(self.version) is not int or self.version != TRANSACTION_VERSION:
            return False
        if self.network != NETWORK_ID or not isinstance(self.network, str):
            return False
        if type(self.amount) is not int or self.amount <= 0:
            return False
        if type(self.fee) is not int or self.fee < 0:
            return False
        if type(self.nonce) is not int or self.nonce < 0:
            return False
        for address in (self.sender, self.recipient, self.public_key):
            if not isinstance(address, str) or not _HEX64.fullmatch(address):
                return False
        if not isinstance(self.signature, str) or not _HEX128.fullmatch(self.signature):
            return False
        try:
            public_key_bytes = bytes.fromhex(self.public_key)
            expected_sender = hashlib.sha256(public_key_bytes).hexdigest()
            if self.sender != expected_sender:
                return False
            key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            key.verify(bytes.fromhex(self.signature), self.signing_bytes())
            return True
        except (ValueError, InvalidSignature, TypeError):
            return False

    @classmethod
    def from_dict(cls, data: dict) -> "Transaction":
        if not isinstance(data, dict):
            raise TypeError("Transaction must be a JSON object")
        keys = {
            "version", "network", "sender", "recipient", "amount", "fee",
            "nonce", "public_key", "signature",
        }
        if set(data) != keys:
            raise ValueError("Transaction must contain exactly the expected fields")
        return cls(**data)


def create_signed_transaction(
    sender_wallet: Wallet,
    recipient: str,
    amount: int,
    fee: int,
    nonce: int,
    network: str = NETWORK_ID,
) -> Transaction:
    """Create and sign a transaction; business validation comes in step 2."""
    if not isinstance(recipient, str) or not _HEX64.fullmatch(recipient):
        raise ValueError("Recipient must be a 64-character lowercase hex address")
    if type(amount) is not int or amount <= 0:
        raise ValueError("Amount must be a positive integer in minimal units")
    if type(fee) is not int or fee < 0:
        raise ValueError("Fee must be a nonnegative integer in minimal units")
    if type(nonce) is not int or nonce < 0:
        raise ValueError("Nonce must be a nonnegative integer")
    if network != NETWORK_ID:
        raise ValueError("Unsupported network")

    unsigned = Transaction(
        version=TRANSACTION_VERSION,
        network=network,
        sender=sender_wallet.address,
        recipient=recipient,
        amount=amount,
        fee=fee,
        nonce=nonce,
        public_key=sender_wallet.public_key_hex,
        signature="",
    )
    signature = sender_wallet.sign(unsigned.signing_bytes()).hex()
    return Transaction(**{**unsigned.unsigned_payload(), "signature": signature})
