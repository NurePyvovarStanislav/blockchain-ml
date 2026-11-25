"""Step 2: confirmed account state of the educational blockchain.

The chain layer reconstructs this state from genesis and validated blocks.
`initial_balances` remains available for isolated Step 2–4 demos/tests only.
"""

from dataclasses import dataclass
from collections.abc import Mapping
import re

from .transaction import Transaction
from .coinbase import CoinbaseTransaction

_ADDRESS = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class AccountState:
    balance: int = 0
    next_nonce: int = 0


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    code: str
    reason: str


OK = ValidationResult(True, "accepted", "Transaction accepted")


def rejected(code: str, reason: str) -> ValidationResult:
    return ValidationResult(False, code, reason)


class LedgerState:
    """Confirmed balances/nonces and IDs of already executed transactions.

    Fee amounts are held in `unclaimed_fees` until the validated block
    settles its coinbase; they are not destroyed or paid out twice.
    """

    def __init__(self, initial_balances: Mapping[str, int] | None = None):
        self._accounts: dict[str, AccountState] = {}
        self._confirmed_txids: set[str] = set()
        self._unclaimed_fees = 0

        for address, balance in (initial_balances or {}).items():
            if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
                raise ValueError("Initial account address must be 64 lowercase hex chars")
            if type(balance) is not int or balance < 0:
                raise ValueError("Initial balance must be a non-negative integer")
            self._accounts[address] = AccountState(balance=balance)

    def account(self, address: str) -> AccountState:
        return self._accounts.get(address, AccountState())

    def balance(self, address: str) -> int:
        return self.account(address).balance

    def next_nonce(self, address: str) -> int:
        return self.account(address).next_nonce

    def was_confirmed(self, txid: str) -> bool:
        return txid in self._confirmed_txids

    @property
    def unclaimed_fees(self) -> int:
        return self._unclaimed_fees

    def validate_for_confirmation(self, tx: Transaction) -> ValidationResult:
        """Check a transaction against confirmed state only (not the mempool)."""
        if not isinstance(tx, Transaction) or not tx.verify_signature():
            return rejected("invalid_signature", "Invalid transaction format or signature")
        if self.was_confirmed(tx.txid):
            return rejected("duplicate", "Transaction is already confirmed")

        sender = self.account(tx.sender)
        if tx.nonce != sender.next_nonce:
            return rejected(
                "invalid_nonce",
                f"Expected sender nonce {sender.next_nonce}, got {tx.nonce}",
            )

        required = tx.amount + tx.fee
        if required > sender.balance:
            return rejected(
                "insufficient_funds",
                f"Sender has {sender.balance}, but needs {required} including fee",
            )
        return OK

    def apply_confirmed(self, tx: Transaction) -> ValidationResult:
        """Apply a normal transfer to a working state after its checks.

        All checks occur before mutating balances/nonces/confirmed IDs.
        """
        outcome = self.validate_for_confirmation(tx)
        if not outcome.accepted:
            return outcome

        sender = self.account(tx.sender)
        recipient = self.account(tx.recipient)
        sender_balance = sender.balance - tx.amount - tx.fee

        if tx.sender == tx.recipient:
            # A self-transfer changes the balance only by its fee.
            self._accounts[tx.sender] = AccountState(
                balance=sender_balance + tx.amount,
                next_nonce=sender.next_nonce + 1,
            )
        else:
            self._accounts[tx.sender] = AccountState(
                balance=sender_balance,
                next_nonce=sender.next_nonce + 1,
            )
            self._accounts[tx.recipient] = AccountState(
                balance=recipient.balance + tx.amount,
                next_nonce=recipient.next_nonce,
            )

        self._unclaimed_fees += tx.fee
        self._confirmed_txids.add(tx.txid)
        return ValidationResult(True, "confirmed", "Transaction applied to confirmed state")

    def copy(self) -> "LedgerState":
        """Independent working state; AccountState values are immutable."""
        clone = LedgerState()
        clone._accounts = self._accounts.copy()
        clone._confirmed_txids = self._confirmed_txids.copy()
        clone._unclaimed_fees = self._unclaimed_fees
        return clone

    def _apply_coinbase(self, tx: CoinbaseTransaction, subsidy: int) -> ValidationResult:
        """Internal settlement: chain validation must authorize height and PoW first.

        Called only after all normal transactions in this block have succeeded.
        The fee accumulator must have been zero at the beginning of the block.
        """
        if type(tx) is not CoinbaseTransaction or type(subsidy) is not int or subsidy < 1:
            return rejected("invalid_coinbase", "Invalid coinbase or subsidy")
        if self.was_confirmed(tx.txid):
            return rejected("duplicate", "Coinbase is already confirmed")
        if tx.amount != subsidy + self._unclaimed_fees:
            return rejected("invalid_coinbase", "Reward must equal subsidy plus collected fees")
        account = self.account(tx.recipient)
        self._accounts[tx.recipient] = AccountState(
            balance=account.balance + tx.amount,
            next_nonce=account.next_nonce,
        )
        self._confirmed_txids.add(tx.txid)
        self._unclaimed_fees = 0
        return ValidationResult(True, "confirmed", "Miner reward credited")
