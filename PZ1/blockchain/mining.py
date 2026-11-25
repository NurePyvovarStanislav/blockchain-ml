"""Step 4: PoW search, coinbase construction, and partial mined-block checks.

NOT yet a complete consensus validator: Step 5 must replay account balances,
nonces and the full chain from the common genesis; Step 6 adds fork choice.
"""

from dataclasses import dataclass, replace
from collections.abc import Sequence
from time import perf_counter
import re

from .block import Block
from .coinbase import CoinbaseTransaction
from .difficulty import MiningPolicy
from .transaction import Transaction

_ADDRESS = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class MiningResult:
    block: Block
    attempts: int
    elapsed_seconds: float

    @property
    def hash_rate(self) -> float:
        return self.attempts / self.elapsed_seconds if self.elapsed_seconds else float("inf")


def has_valid_pow(block: Block) -> bool:
    """Check the educational PoW rule (not a full block-validity check)."""
    if not isinstance(block, Block):
        return False
    difficulty = block.header.difficulty
    if difficulty < 1:
        return False  # difficulty 0 is reserved for the undug genesis block
    return block.hash.startswith("0" * difficulty)


def create_mining_candidate(
    history: Sequence[Block],
    transactions: Sequence[Transaction],
    miner_address: str,
    timestamp: int,
    policy: MiningPolicy,
    *,
    extra_nonce: int = 0,
) -> Block:
    """Create block with a coinbase in position 0; does NOT mine it.

    Transactions are assumed to come from an admissible local mempool. Step 5
    independently verifies balances/nonce before accepting any mined block.
    """
    if not isinstance(policy, MiningPolicy):
        raise TypeError("policy must be MiningPolicy")
    if not history:
        raise ValueError("Cannot create a candidate without genesis")
    if not isinstance(miner_address, str) or not _ADDRESS.fullmatch(miner_address):
        raise ValueError("Invalid miner address")
    if type(timestamp) is not int or timestamp <= history[-1].header.timestamp:
        raise ValueError("Block timestamp must be a greater integer than the parent timestamp")
    txs = tuple(transactions)
    if any(not isinstance(tx, Transaction) or not tx.verify_signature() for tx in txs):
        raise ValueError("A candidate includes a malformed or unsigned transaction")
    txids = [tx.txid for tx in txs]
    if len(txids) != len(set(txids)):
        raise ValueError("Duplicate transaction identifiers in one candidate")

    height = history[-1].header.height + 1
    difficulty = policy.expected_next_difficulty(history)
    coinbase = CoinbaseTransaction(
        height=height,
        recipient=miner_address,
        amount=policy.fixed_subsidy + sum(tx.fee for tx in txs),
        extra_nonce=extra_nonce,
    )
    return Block.create(
        height=height,
        timestamp=timestamp,
        previous_hash=history[-1].hash,
        transactions=(coinbase, *txs),
        difficulty=difficulty,
        nonce=0,
    )


def mine_block(candidate: Block, *, max_attempts: int = 1_000_000) -> MiningResult:
    """Brute-force nonce search up to `max_attempts` from candidate nonce.

    A maximum attempt count prevents accidentally freezing the demonstration.
    If exhausted, the caller can retry with a new candidate/timestamp/extra_nonce.
    """
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if candidate.header.height < 1 or candidate.header.difficulty < 1:
        raise ValueError("Only regular blocks with positive difficulty can be mined")
    if not candidate.has_valid_merkle_root():
        raise ValueError("Candidate Merkle root does not match its transactions")
    if not isinstance(candidate.transactions[0] if candidate.transactions else None, CoinbaseTransaction):
        raise ValueError("Mined block must start with a coinbase transaction")

    started = perf_counter()
    initial_nonce = candidate.header.nonce
    last = min(2**64 - 1, initial_nonce + max_attempts - 1)
    for nonce in range(initial_nonce, last + 1):
        block = replace(candidate, header=replace(candidate.header, nonce=nonce))
        if has_valid_pow(block):
            return MiningResult(
                block=block, attempts=nonce - initial_nonce + 1,
                elapsed_seconds=perf_counter() - started,
            )
    raise RuntimeError(f"No solution in {last - initial_nonce + 1} attempts; enlarge max_attempts or change candidate")


def validate_mined_block_structure(
    block: Block,
    history: Sequence[Block],
    policy: MiningPolicy,
) -> bool:
    """Check PoW + the required coinbase reward + structure, NOT balances.

    Caller MUST separately validate confirmed balances, sender nonces, genesis,
    previous history and timestamp rules in Step 5 before accepting a block.
    """
    if not isinstance(block, Block) or not history or not isinstance(policy, MiningPolicy):
        return False
    parent = history[-1]
    try:
        expected_difficulty = policy.expected_next_difficulty(history)
    except (TypeError, ValueError, IndexError):
        return False
    if not block.is_linked_to(parent):
        return False
    if block.header.timestamp <= parent.header.timestamp:
        return False
    if block.header.difficulty != expected_difficulty:
        return False
    if not block.has_valid_merkle_root() or not has_valid_pow(block):
        return False

    txs = block.transactions
    if not txs or not isinstance(txs[0], CoinbaseTransaction):
        return False
    coinbase = txs[0]
    if coinbase.height != block.header.height:
        return False
    normal = txs[1:]
    if any(not isinstance(tx, Transaction) or not tx.verify_signature() for tx in normal):
        return False
    ids = [tx.txid for tx in normal]
    if len(ids) != len(set(ids)):
        return False
    if coinbase.amount != policy.fixed_subsidy + sum(tx.fee for tx in normal):  # type: ignore
        return False
    return True
