"""Step 3: deterministic Merkle root from transaction IDs.

Educational PZ1 rules (NOT Bitcoin-compatible):
    leaf   = SHA256(0x00 || 32 raw bytes of TXID)
    parent = SHA256(0x01 || left_digest || right_digest)
    empty  = SHA256(0x02)
If a level has an odd number of nodes, its last hash is duplicated.
The ordering of TXIDs matters. This routine commits to identifiers, and
transaction validity must be checked separately by later steps.
"""

import hashlib
import re
from collections.abc import Sequence

_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _txid_bytes(txid: str) -> bytes:
    if not isinstance(txid, str) or not _HEX_64.fullmatch(txid):
        raise ValueError("TXID must be exactly 64 lowercase hexadecimal characters")
    return bytes.fromhex(txid)


def leaf_hash(txid: str) -> bytes:
    """Domain-separated leaf representing one signed transaction ID."""
    return _sha256(b"\x00" + _txid_bytes(txid))


def parent_hash(left: bytes, right: bytes) -> bytes:
    if not isinstance(left, bytes) or len(left) != 32:
        raise ValueError("Left digest must contain 32 bytes")
    if not isinstance(right, bytes) or len(right) != 32:
        raise ValueError("Right digest must contain 32 bytes")
    return _sha256(b"\x01" + left + right)


def merkle_root(txids: Sequence[str]) -> str:
    """Return a 64-hex-character commitment to the ORDERED TXIDs.

    The empty-tree root is a dedicated protocol constant, not the root of an
    actual transaction. No Merkle proof functionality is claimed at Step 3.
    """
    if isinstance(txids, (str, bytes)):
        raise TypeError("TXIDs must be a sequence of hexadecimal strings")

    level = [leaf_hash(txid) for txid in txids]
    if not level:
        return _sha256(b"\x02").hex()

    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])  # required by the assignment
        level = [
            parent_hash(level[i], level[i + 1])
            for i in range(0, len(level), 2)
        ]
    return level[0].hex()
