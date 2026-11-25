"""Step 4: special miner reward transaction (not signed by a sender).

Consensus rules for the educational PZ1 network:
- Coinbase is the FIRST transaction in each non-genesis mined block.
- The amount is exactly `fixed_subsidy + fees_of_normal_transactions`.
- Its recipient is a lowercase 64-hex wallet address.
- There is no normal sender, signature, or fee in a coinbase transaction.

Balance crediting from this transaction is deliberately deferred to Step 5.
"""

from dataclasses import dataclass
import hashlib
import re

from .config import NETWORK_ID
from .serialization import canonical_bytes

_ADDRESS = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class CoinbaseTransaction:
    height: int
    recipient: str
    amount: int
    extra_nonce: int = 0

    def __post_init__(self) -> None:
        if type(self.height) is not int or self.height < 1:
            raise ValueError("Coinbase height must be a positive integer")
        if not isinstance(self.recipient, str) or not _ADDRESS.fullmatch(self.recipient):
            raise ValueError("Coinbase recipient must be a 64-character lowercase HEX address")
        if type(self.amount) is not int or self.amount < 1:
            raise ValueError("Coinbase amount must be a positive integer")
        if type(self.extra_nonce) is not int or not 0 <= self.extra_nonce <= 2**64 - 1:
            raise ValueError("Coinbase extra_nonce must be an unsigned 64-bit integer")

    def to_dict(self) -> dict:
        return {
            "type": "coinbase",
            "version": 1,
            "network": NETWORK_ID,
            "height": self.height,
            "recipient": self.recipient,
            "amount": self.amount,
            "extra_nonce": self.extra_nonce,
        }

    @property
    def txid(self) -> str:
        return hashlib.sha256(canonical_bytes(self.to_dict())).hexdigest()


# Even when no user transactions are included, a miner may claim the subsidy.
