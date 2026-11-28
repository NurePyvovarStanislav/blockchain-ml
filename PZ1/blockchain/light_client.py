"""Header-only client and Merkle inclusion proof for the PZ1 tree.

Proof of inclusion is not proof of transaction validity or global finality.
The specified odd-leaf duplication convention does not commit leaf_count.
"""
from dataclasses import dataclass
from collections.abc import Sequence
from time import time

from .block import Block, BlockHeader
from .chain import create_genesis, MAX_FUTURE_SECONDS, POLICY
from .merkle import leaf_hash, parent_hash


@dataclass(frozen=True)
class MerkleProof:
    index: int
    leaf_count: int
    siblings: tuple[str, ...]


def make_proof(txids: Sequence[str], index: int) -> MerkleProof:
    if isinstance(txids, (str, bytes)) or type(index) is not int or not 0 <= index < len(txids):
        raise ValueError("Invalid transaction sequence or index")
    level = [leaf_hash(txid) for txid in txids]
    position, siblings = index, []
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        siblings.append(level[position ^ 1].hex())
        level = [parent_hash(level[j], level[j + 1])
                 for j in range(0, len(level), 2)]
        position //= 2
    return MerkleProof(index, len(txids), tuple(siblings))


def verify_proof(txid: str, proof: MerkleProof, root: str) -> bool:
    """Recompute the root using sibling hashes and their implicit left/right sides."""
    try:
        if (type(proof) is not MerkleProof
                or type(proof.index) is not int or type(proof.leaf_count) is not int
                or not 0 <= proof.index < proof.leaf_count <= 2**64 - 1
                or type(proof.siblings) is not tuple
                or len(proof.siblings) != (proof.leaf_count - 1).bit_length()):
            return False
        current = leaf_hash(txid)
        pos, count = proof.index, proof.leaf_count
        for value in proof.siblings:
            if type(value) is not str or len(value) != 64:
                return False
            sibling = bytes.fromhex(value)
            if sibling.hex() != value:
                return False
            # If last odd leaf is duplicated, the sibling must equal itself.
            if count % 2 and pos == count - 1 and sibling != current:
                return False
            current = (parent_hash(sibling, current) if pos % 2
                       else parent_hash(current, sibling))
            pos //= 2
            count = (count + 1) // 2
        return current.hex() == root
    except (TypeError, ValueError):
        return False


class LightClient:
    """Store only immutable headers; verify PoW/links/retarget/timestamps."""
    def __init__(self, headers: Sequence[BlockHeader], *, now=None):
        headers = tuple(headers)
        if not headers or headers[0] != create_genesis().header:
            raise ValueError("Invalid genesis header")
        now = int(time()) if now is None else now
        # Empty-body wrappers are temporary: reuse the same difficulty rules.
        prefix = [Block(headers[0], ())]
        for h in headers[1:]:
            previous = prefix[-1].header
            if (type(h) is not BlockHeader or h.height != previous.height + 1
                    or h.previous_hash != previous.hash
                    or not previous.timestamp < h.timestamp <= now + MAX_FUTURE_SECONDS
                    or h.difficulty != POLICY.expected_next_difficulty(prefix)
                    or not h.hash.startswith("0" * h.difficulty)):
                raise ValueError("Invalid header chain")
            prefix.append(Block(h, ()))
        self.headers = headers

    def includes(self, height: int, txid: str, proof: MerkleProof) -> bool:
        if type(height) is not int or not 0 <= height < len(self.headers):
            return False
        return verify_proof(txid, proof, self.headers[height].merkle_root)
