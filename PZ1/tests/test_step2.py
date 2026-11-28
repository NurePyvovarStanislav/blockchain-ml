"""Step 2 regression tests; run with: python -m pytest -q"""
from dataclasses import replace
import pytest

from blockchain.mempool import Mempool
from blockchain.state import LedgerState
from blockchain.transaction import create_signed_transaction
from blockchain.wallet import Wallet


@pytest.fixture
def env():
    alice, bob, charlie = Wallet.generate(), Wallet.generate(), Wallet.generate()
    ledger = LedgerState({alice.address: 100, bob.address: 0, charlie.address: 0})
    pool = Mempool()
    return alice, bob, charlie, ledger, pool


def make(sender, target, amount=20, fee=1, nonce=0):
    return create_signed_transaction(sender, target.address, amount, fee, nonce)


def test_signature_tamper_rejected(env):
    a, b, _, ledger, pool = env
    tx = make(a, b)
    result = pool.add(replace(tx, amount=40), ledger)
    assert result.code == "invalid_signature" and len(pool) == 0


def test_duplicate_in_mempool_rejected(env):
    a, b, _, ledger, pool = env
    tx = make(a, b)
    assert pool.add(tx, ledger).accepted
    assert pool.add(tx, ledger).code == "duplicate"
    assert len(pool) == 1


def test_unconfirmed_incoming_cannot_be_spent(env):
    a, b, c, ledger, pool = env
    assert pool.add(make(a, b), ledger).accepted
    result = pool.add(make(b, c, amount=1, fee=0, nonce=0), ledger)
    assert result.code == "insufficient_funds"


def test_reconcile_removes_confirmed_and_keeps_next_nonce(env):
    a, b, c, ledger, pool = env
    tx0 = make(a, b, amount=70, fee=2, nonce=0)
    tx1 = make(a, c, amount=20, fee=1, nonce=1)
    assert pool.add(tx0, ledger).accepted
    assert pool.add(tx1, ledger).accepted
    assert ledger.apply_confirmed(tx0).accepted
    dropped = pool.reconcile(ledger)
    assert len(dropped) == 1 and dropped[0][1].code == "duplicate"
    assert pool.transactions() == (tx1,)
    assert pool.expected_nonce(a.address, ledger) == 2
    assert pool.available_balance(a.address, ledger) == 7

def test_nonce_is_contiguous_and_per_sender(env):
    a, b, c, ledger, pool = env
    assert pool.add(make(a, b, nonce=2), ledger).code == "invalid_nonce"
    assert pool.add(make(a, b, nonce=0), ledger).accepted
    assert pool.add(make(a, c, nonce=0), ledger).code == "invalid_nonce"
    assert pool.add(make(a, c, nonce=1), ledger).accepted
    assert pool.expected_nonce(a.address, ledger) == 2
    assert pool.expected_nonce(b.address, ledger) == 0


def test_balance_reserves_amount_and_fee_without_confirmation(env):
    a, b, c, ledger, pool = env
    assert pool.add(make(a, b, amount=70, fee=2), ledger).accepted
    assert pool.reserved(a.address) == 72
    assert pool.available_balance(a.address, ledger) == 28
    assert pool.add(make(a, c, amount=28, fee=1, nonce=1), ledger).code == "insufficient_funds"
    assert pool.add(make(a, c, amount=27, fee=1, nonce=1), ledger).accepted
    assert pool.available_balance(a.address, ledger) == 0
    assert ledger.balance(a.address) == 100 and ledger.balance(b.address) == 0
    assert ledger.next_nonce(a.address) == 0


def test_confirmation_updates_state_and_prevents_replay(env):
    a, b, c, ledger, pool = env
    first = make(a, b, amount=70, fee=2)
    assert ledger.apply_confirmed(first).accepted
    assert ledger.balance(a.address) == 28 and ledger.balance(b.address) == 70
    assert ledger.next_nonce(a.address) == 1 and ledger.unclaimed_fees == 2
    assert pool.add(first, ledger).code == "duplicate"
    assert pool.add(make(a, c, nonce=0), ledger).code == "invalid_nonce"
