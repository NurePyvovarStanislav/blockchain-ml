"""Step 5: canonical history and replay-based validation (single process).

Network fork choice and concurrency are deliberately left to Step 6.
"""
from collections.abc import Sequence
from pathlib import Path
from time import time

from .block import Block, BlockHeader, ZERO_HASH
from .coinbase import CoinbaseTransaction
from .difficulty import MiningPolicy
from .mempool import Mempool
from .mining import (MiningResult, create_mining_candidate, has_valid_pow,
                     mine_block, validate_mined_block_structure)
from .state import LedgerState, ValidationResult, OK, rejected
from .transaction import Transaction

# Protocol constants: identical on all nodes of this educational network.
POLICY = MiningPolicy()
GENESIS_TIMESTAMP = 1_700_000_000
MAX_FUTURE_SECONDS = 120


def create_genesis() -> Block:
    return Block.create(height=0, timestamp=GENESIS_TIMESTAMP,
                        previous_hash=ZERO_HASH, transactions=(), difficulty=0, nonce=0)


class ChainValidationError(ValueError):
    def __init__(self, result: ValidationResult):
        self.result = result
        super().__init__(f"{result.code}: {result.reason}")


def _advance(block: Block, history: Sequence[Block], state: LedgerState,
             now: int) -> LedgerState:
    """Return a new state or raise; never mutate the supplied accepted state."""
    def fail(code: str, reason: str) -> None:
        raise ChainValidationError(rejected(code, reason))

    if type(block) is not Block or type(block.header) is not BlockHeader:
        fail("invalid_block", "Expected a block with a real BlockHeader")
    if not block.is_linked_to(history[-1]):
        fail("invalid_link", "Wrong block height or previous hash")
    if not history[-1].header.timestamp < block.header.timestamp <= now + MAX_FUTURE_SECONDS:
        fail("invalid_timestamp", "Timestamp must increase and not exceed local time + 120 seconds")
    if block.header.difficulty != POLICY.expected_next_difficulty(history):
        fail("invalid_difficulty", "Difficulty differs from the expected history-based value")
    if not block.transactions or type(block.transactions[0]) is not CoinbaseTransaction:
        fail("invalid_coinbase", "First transaction must be coinbase")
    if any(type(tx) is not Transaction for tx in block.transactions[1:]):
        fail("invalid_transaction_type", "Only ordinary transfers may follow coinbase")
    # Reject malformed transfers before computing TXIDs / Merkle root.
    if any(not tx.verify_signature() for tx in block.transactions[1:]): # type: ignore
        fail("invalid_signature", "Invalid transaction signature or format")
    if not block.has_valid_merkle_root():
        fail("invalid_merkle", "Transaction list does not match Merkle root")
    if not has_valid_pow(block):
        fail("invalid_pow", "Header hash does not satisfy Proof of Work")
    # Step 4 checks: link, timestamps, expected difficulty, PoW, Merkle,
    # ordinary signatures, unique TXIDs and exact reward for this height.
    if not validate_mined_block_structure(block, history, POLICY):
        fail("invalid_structure", "Invalid coinbase height/reward or duplicate TXID")
    if state.unclaimed_fees != 0:
        fail("invalid_state", "Previous block must have settled all fees")

    working = state.copy()
    # Coinbase is FIRST in the serialized block, but credited LAST in state.
    # No spending a reward of the same block to fund its own transfers.
    for tx in block.transactions[1:]:
        outcome = working.apply_confirmed(tx) # type: ignore
        if not outcome.accepted:
            fail(outcome.code, outcome.reason)
    outcome = working._apply_coinbase(block.transactions[0], POLICY.fixed_subsidy) # type: ignore
    if not outcome.accepted:
        fail(outcome.code, outcome.reason)
    return working


def replay_chain(blocks: Sequence[Block], *, now: int | None = None) -> LedgerState:
    """Rebuild all balances, nonces and confirmed IDs from common genesis."""
    if not blocks or type(blocks[0]) is not Block or blocks[0] != create_genesis():
        raise ChainValidationError(rejected("invalid_genesis", "Genesis must equal the protocol constant"))
    checked_at = int(time()) if now is None else now
    state = LedgerState()  # no preallocated money and no trusted balance file
    prefix = [blocks[0]]
    for block in blocks[1:]:
        try:
            state = _advance(block, prefix, state, checked_at)
        except ChainValidationError as exc:
            raise ChainValidationError(rejected(exc.result.code,
                f"Block at index {len(prefix)}: {exc.result.reason}")) from exc
        prefix.append(block)
    return state


def validate_chain(blocks: Sequence[Block], *, now: int | None = None) -> ValidationResult:
    try:
        replay_chain(blocks, now=now)
    except ChainValidationError as exc:
        return exc.result
    return ValidationResult(True, "valid_chain", "All blocks validated from genesis")


class Blockchain:
    """Owns the accepted history, state and local mempool; single-threaded API."""
    def __init__(self, blocks: Sequence[Block] | None = None):
        history = tuple(blocks) if blocks is not None else (create_genesis(),)
        self._state = replay_chain(history)
        self._blocks = list(history)
        self._pool = Mempool()

    @property
    def blocks(self) -> tuple[Block, ...]:
        return tuple(self._blocks)

    @property
    def tip(self) -> Block:
        return self._blocks[-1]

    @property
    def ledger(self) -> LedgerState:
        return self._state.copy()  # external callers cannot mutate accepted state

    def balance(self, address: str) -> int:
        return self._state.balance(address)

    def pending(self) -> tuple[Transaction, ...]:
        return self._pool.transactions()

    def submit_transaction(self, tx: Transaction) -> ValidationResult:
        return self._pool.add(tx, self._state)

    def add_block(self, block: Block) -> ValidationResult:
        """Validate against the trusted prefix, then publish state and reconcile."""
        try:
            working = _advance(block, self._blocks, self._state, int(time()))
        except ChainValidationError as exc:
            return exc.result
        self._blocks.append(block)
        self._state = working
        self._pool.reconcile(self._state)
        return ValidationResult(True, "accepted_block", "Block and state accepted")

    def mine_pending(self, miner_address: str, *, timestamp: int | None = None,
                     max_attempts: int = 1_000_000) -> MiningResult:
        if timestamp is None:
            timestamp = max(int(time()), self.tip.header.timestamp + 1)
        if type(timestamp) is not int or timestamp > int(time()) + MAX_FUTURE_SECONDS:
            raise ValueError("Invalid mining timestamp")
        self._pool.reconcile(self._state)
        candidate = create_mining_candidate(self._blocks, self.pending(), miner_address,
                                            timestamp, POLICY)
        result = mine_block(candidate, max_attempts=max_attempts)
        outcome = self.add_block(result.block)
        if not outcome.accepted:
            raise ChainValidationError(outcome)
        return result

    def validate(self) -> ValidationResult:
        return validate_chain(self.blocks)

    def save(self, path: str | Path) -> None:
        from .storage import save_blocks
        save_blocks(self.blocks, path)

    @classmethod
    def load(cls, path: str | Path) -> "Blockchain":
        from .storage import load_blocks
        return cls(load_blocks(path))  # independent validation before returning
