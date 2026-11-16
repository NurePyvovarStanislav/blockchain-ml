"""Step 2: admission of pending transactions with four mandatory checks.

Policy: accept only contiguous nonces, reserve amount + fee for each pending
transaction from the sender, and do not count unconfirmed incoming transfers.
The pool is local to one node. It must be reconciled after a chain update/fork.
"""

from .state import LedgerState, ValidationResult, OK, rejected
from .transaction import Transaction


class Mempool:
    def __init__(self):
        # dict preserves insertion order on supported Python 3 versions.
        self._pending: dict[str, Transaction] = {}

    def transactions(self) -> tuple[Transaction, ...]:
        return tuple(self._pending.values())

    def __len__(self) -> int:
        return len(self._pending)

    def reserved(self, sender: str) -> int:
        return sum(
            tx.amount + tx.fee
            for tx in self._pending.values()
            if tx.sender == sender
        )

    def available_balance(self, sender: str, ledger: LedgerState) -> int:
        return ledger.balance(sender) - self.reserved(sender)

    def expected_nonce(self, sender: str, ledger: LedgerState) -> int:
        return ledger.next_nonce(sender) + sum(
            1 for tx in self._pending.values() if tx.sender == sender
        )

    def _admit(self, tx: Transaction, ledger: LedgerState) -> ValidationResult:
        # Check 1: cryptographic signature AND transaction format.
        if not isinstance(tx, Transaction) or not tx.verify_signature():
            return rejected("invalid_signature", "Invalid transaction format or signature")

        # Check 2: duplicates in this mempool or in confirmed history.
        if tx.txid in self._pending:
            return rejected("duplicate", "Transaction is already in the mempool")
        if ledger.was_confirmed(tx.txid):
            return rejected("duplicate", "Transaction is already confirmed")

        # Check 3: strictly sequential sender nonce including pending items.
        expected = self.expected_nonce(tx.sender, ledger)
        if tx.nonce != expected:
            return rejected(
                "invalid_nonce", f"Expected sender nonce {expected}, got {tx.nonce}"
            )

        # Check 4: confirmed balance minus reserved outgoing value+fees.
        available = self.available_balance(tx.sender, ledger)
        required = tx.amount + tx.fee
        if required > available:
            return rejected(
                "insufficient_funds",
                f"Available {available}, but needs {required} including fee",
            )

        self._pending[tx.txid] = tx
        return OK

    def add(self, tx: Transaction, ledger: LedgerState) -> ValidationResult:
        # Confirmed state may have changed since the last request.
        # Revalidate existing pending transactions before checking a new one.
        self.reconcile(ledger)
        return self._admit(tx, ledger)

    def reconcile(self, ledger: LedgerState) -> list[tuple[str, ValidationResult]]:
        """Recheck after confirmations/reorganizations; remove stale conflicts.

        Returns a list of (txid, rejection result) for dropped transactions.
        Future network code must call this when replacing the canonical chain.
        """
        previous = tuple(self._pending.values())
        self._pending.clear()
        dropped: list[tuple[str, ValidationResult]] = []
        for tx in previous:
            outcome = self._admit(tx, ledger)
            if not outcome.accepted:
                dropped.append((tx.txid, outcome))
        return dropped
