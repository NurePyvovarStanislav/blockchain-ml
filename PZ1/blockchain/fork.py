"""Step 6: choose a validated history by cumulative expected PoW work."""
from collections.abc import Sequence
from dataclasses import dataclass

from .block import Block
from .chain import Blockchain, ChainValidationError
from .state import ValidationResult, rejected
from .transaction import Transaction


def chain_work(blocks: Sequence[Block]) -> int:
    """Score an ALREADY validated history; genesis contributes no work."""
    return sum(16 ** b.header.difficulty for b in blocks[1:])


@dataclass(frozen=True)
class ForkResult:
    chain: Blockchain
    outcome: ValidationResult
    orphaned: tuple[Transaction, ...] = ()
    recovered: tuple[Transaction, ...] = ()
    dropped: tuple[tuple[str, str], ...] = ()
    common_height: int = 0


def choose_chain(current: Blockchain, blocks: Sequence[Block]) -> ForkResult:
    """Do not mutate current. Validate candidate, then rebuild a fresh mempool."""
    try:
        candidate = Blockchain(blocks)
    except ChainValidationError as exc:
        return ForkResult(current, exc.result)
    if chain_work(candidate.blocks) <= chain_work(current.blocks):
        return ForkResult(current, rejected('not_more_work', 'Keep local chain on equal or lower work'))

    common = 0
    for a, b in zip(current.blocks, candidate.blocks):
        if a.hash != b.hash:
            break
        common = a.header.height
    ledger = candidate.ledger
    orphaned = tuple(tx for block in current.blocks[common + 1:]
                     for tx in block.transactions if type(tx) is Transaction
                     and not ledger.was_confirmed(tx.txid))
    # Prioritize orphaned transfers over conflicting local pending transfers.
    # Group by sender and nonce to avoid losing nonce n+1 merely because n
    # was in an orphaned block and n+1 was already pending.
    unique = {tx.txid: tx for tx in (*orphaned, *current.pending())
              if not ledger.was_confirmed(tx.txid)}
    ordered = sorted(unique.values(), key=lambda tx: (tx.sender, tx.nonce))
    dropped = []
    for tx in ordered:
        result = candidate.submit_transaction(tx)
        if not result.accepted:
            dropped.append((tx.txid, result.code))
    pending_ids = {tx.txid for tx in candidate.pending()}
    recovered = tuple(tx for tx in orphaned if tx.txid in pending_ids)
    return ForkResult(candidate, ValidationResult(True, 'adopted_chain', 'Valid chain with greater work'),
                      orphaned, recovered, tuple(dropped), common)
