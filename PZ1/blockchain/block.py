"""Step 3: block header, ordered transactions and structural checks.

This is not a full blockchain validator: no PoW search/check, miner reward,
balance transitions or fork choice until later steps. The immutable header
is hashed using SHA-256 of deterministic JSON bytes (a PZ1 protocol choice).
"""

from dataclasses import dataclass
import hashlib
import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .merkle import merkle_root
from .serialization import canonical_bytes

_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
ZERO_HASH = "0" * 64


@runtime_checkable
class BlockTransaction(Protocol):
    """Minimal block transaction interface.

    Currently provided by signed Transaction. Step 4 can add CoinbaseTransaction
    implementing the same public methods without changing the block model.
    """

    @property
    def txid(self) -> str: ...

    def to_dict(self) -> dict: ...


def _hash_field(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HEX_64.fullmatch(value):
        raise ValueError(f"{name} must contain exactly 64 lowercase hex characters")


@dataclass(frozen=True)
class BlockHeader:
    height: int
    timestamp: int  # Unix timestamp, seconds; NOT the time of each transaction
    previous_hash: str
    merkle_root: str
    difficulty: int  # Step 4: leading hexadecimal zeroes in the block hash
    nonce: int       # Step 4: value adjusted by the miner

    def __post_init__(self) -> None:
        if type(self.height) is not int or self.height < 0:
            raise ValueError("height must be a non-negative integer")
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ValueError("timestamp must be a non-negative integer")
        _hash_field(self.previous_hash, "previous_hash")
        _hash_field(self.merkle_root, "merkle_root")
        if type(self.difficulty) is not int or not 0 <= self.difficulty <= 64:
            raise ValueError("difficulty must be an integer from 0 to 64")
        if type(self.nonce) is not int or not 0 <= self.nonce <= 2**64 - 1:
            raise ValueError("nonce must be an unsigned 64-bit integer")

    def to_dict(self) -> dict:
        return {
            "height": self.height,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "merkle_root": self.merkle_root,
            "difficulty": self.difficulty,
            "nonce": self.nonce,
        }

    def to_bytes(self) -> bytes:
        """Exact header bytes used to compute its hash."""
        return canonical_bytes(self.to_dict())

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()


@dataclass(frozen=True)
class Block:
    header: BlockHeader
    transactions: tuple[BlockTransaction, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.header, BlockHeader):
            raise TypeError("header must be BlockHeader")
        if not isinstance(self.transactions, tuple):
            raise TypeError("transactions must be a tuple")
        if any(not isinstance(tx, BlockTransaction) for tx in self.transactions):
            raise TypeError("each transaction must have txid and to_dict()")

    @classmethod
    def create(
        cls,
        *,
        height: int,
        timestamp: int,
        previous_hash: str,
        transactions: Sequence[BlockTransaction],
        difficulty: int = 0,
        nonce: int = 0,
    ) -> "Block":
        """Construct a block; it is NOT mined just because difficulty is set."""
        frozen_transactions = tuple(transactions)
        root = merkle_root([tx.txid for tx in frozen_transactions])
        return cls(
            header=BlockHeader(
                height=height,
                timestamp=timestamp,
                previous_hash=previous_hash,
                merkle_root=root,
                difficulty=difficulty,
                nonce=nonce,
            ),
            transactions=frozen_transactions,
        )

    @property
    def hash(self) -> str:
        return self.header.hash

    def has_valid_merkle_root(self) -> bool:
        """Structural commitment check only; NOT full transaction validation."""
        return self.header.merkle_root == merkle_root(
            [tx.txid for tx in self.transactions]
        )

    def is_linked_to(self, previous: "Block") -> bool:
        """Check the height/hash reference; no chain consensus is implied."""
        return (
            isinstance(previous, Block)
            and self.header.height == previous.header.height + 1
            and self.header.previous_hash == previous.hash
        )

    def to_dict(self) -> dict:
        return {
            "header": self.header.to_dict(),
            "transactions": [tx.to_dict() for tx in self.transactions],
            "hash": self.hash,  # informative; recomputable, not a source of truth
        }
