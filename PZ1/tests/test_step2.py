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


def test_valid_signed_transaction_is_admitted(env):
    a, b, _, ledger, pool = env
    assert pool.add(make(a, b), ledger).accepted
    assert len(pool) == 1


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


def test_skipped_nonce_is_rejected(env):
    a, b, _, ledger, pool = env
    result = pool.add(make(a, b, nonce=2), ledger)
    assert result.code == "invalid_nonce"


def test_conflicting_same_nonce_with_different_txid_is_rejected(env):
    a, b, c, ledger, pool = env
    assert pool.add(make(a, b, nonce=0), ledger).accepted
    assert pool.add(make(a, c, nonce=0), ledger).code == "invalid_nonce"


def test_second_contiguous_nonce_accepted(env):
    a, b, c, ledger, pool = env
    assert pool.add(make(a, b, nonce=0), ledger).accepted
    assert pool.add(make(a, c, nonce=1), ledger).accepted
    assert pool.expected_nonce(a.address, ledger) == 2


def test_pending_amount_and_fee_are_reserved(env):
    a, b, c, ledger, pool = env
    assert pool.add(make(a, b, amount=70, fee=2, nonce=0), ledger).accepted
    assert pool.reserved(a.address) == 72
    assert pool.available_balance(a.address, ledger) == 28
    assert pool.add(make(a, c, amount=30, fee=1, nonce=1), ledger).code == "insufficient_funds"
    assert len(pool) == 1


def test_exact_balance_including_fee_allowed(env):
    a, b, _, ledger, pool = env
    assert pool.add(make(a, b, amount=99, fee=1), ledger).accepted
    assert pool.available_balance(a.address, ledger) == 0


def test_no_premature_balance_change_when_pending(env):
    a, b, _, ledger, pool = env
    assert pool.add(make(a, b), ledger).accepted
    assert ledger.balance(a.address) == 100
    assert ledger.balance(b.address) == 0
    assert ledger.next_nonce(a.address) == 0


def test_unconfirmed_incoming_cannot_be_spent(env):
    a, b, c, ledger, pool = env
    assert pool.add(make(a, b), ledger).accepted
    result = pool.add(make(b, c, amount=1, fee=0, nonce=0), ledger)
    assert result.code == "insufficient_funds"


def test_confirmed_transaction_updates_balances_and_nonces(env):
    a, b, _, ledger, _ = env
    tx = make(a, b, amount=70, fee=2)
    assert ledger.apply_confirmed(tx).accepted
    assert ledger.balance(a.address) == 28
    assert ledger.balance(b.address) == 70
    assert ledger.next_nonce(a.address) == 1
    assert ledger.unclaimed_fees == 2


def test_confirmed_txid_duplicate_rejected(env):
    a, b, _, ledger, pool = env
    tx = make(a, b)
    assert ledger.apply_confirmed(tx).accepted
    assert pool.add(tx, ledger).code == "duplicate"
    assert ledger.apply_confirmed(tx).code == "duplicate"


def test_old_nonce_after_confirmation_rejected(env):
    a, b, c, ledger, pool = env
    assert ledger.apply_confirmed(make(a, b, nonce=0)).accepted
    assert pool.add(make(a, c, nonce=0), ledger).code == "invalid_nonce"


def test_confirmation_rejects_overspending_and_does_not_mutate_state(env):
    a, b, _, ledger, _ = env
    too_big = make(a, b, amount=100, fee=1)
    result = ledger.apply_confirmed(too_big)
    assert result.code == "insufficient_funds"
    assert ledger.balance(a.address) == 100
    assert ledger.balance(b.address) == 0
    assert ledger.next_nonce(a.address) == 0
    assert ledger.unclaimed_fees == 0


def test_confirmation_rejects_invalid_signature(env):
    a, b, _, ledger, _ = env
    tx = make(a, b)
    assert ledger.apply_confirmed(replace(tx, fee=2)).code == "invalid_signature"


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


def test_reconcile_drops_conflicts_after_external_confirmation(env):
    a, b, c, ledger, pool = env
    pending = make(a, b, amount=70, fee=1, nonce=0)
    assert pool.add(pending, ledger).accepted
    other_confirmed = make(a, c, amount=80, fee=1, nonce=0)
    assert ledger.apply_confirmed(other_confirmed).accepted
    removed = pool.reconcile(ledger)
    assert len(removed) == 1 and removed[0][1].code == "invalid_nonce"
    assert len(pool) == 0


def test_reconcile_drops_descendant_after_nonce_gap(env):
    a, b, c, ledger, pool = env
    tx0 = make(a, b, amount=70, fee=1, nonce=0)
    tx1 = make(a, c, amount=20, fee=1, nonce=1)
    assert pool.add(tx0, ledger).accepted
    assert pool.add(tx1, ledger).accepted
    other_confirmed = make(a, c, amount=95, fee=1, nonce=0)
    assert ledger.apply_confirmed(other_confirmed).accepted
    dropped = pool.reconcile(ledger)
    assert len(dropped) == 2
    assert len(pool) == 0


def test_self_transfer_only_consumes_fee_on_confirmation(env):
    a, _, _, ledger, _ = env
    tx = make(a, a, amount=40, fee=3, nonce=0)
    assert ledger.apply_confirmed(tx).accepted
    assert ledger.balance(a.address) == 97
    assert ledger.unclaimed_fees == 3


def test_two_different_senders_have_independent_nonces(env):
    a, b, c, ledger, pool = env
    assert ledger.apply_confirmed(make(a, b, amount=50, fee=0, nonce=0)).accepted
    assert pool.add(make(a, c, amount=20, fee=1, nonce=1), ledger).accepted
    assert pool.add(make(b, c, amount=10, fee=0, nonce=0), ledger).accepted
    assert pool.expected_nonce(a.address, ledger) == 2
    assert pool.expected_nonce(b.address, ledger) == 1


@pytest.mark.parametrize("initial", [-1, True, 1.5])
def test_invalid_initial_balances_rejected(env, initial):
    a, _, _, _, _ = env
    with pytest.raises(ValueError):
        LedgerState({a.address: initial})


def test_invalid_initial_address_rejected():
    with pytest.raises(ValueError):
        LedgerState({"Alice": 100})


def test_exposed_account_state_is_immutable(env):
    a, _, _, ledger, _ = env
    account = ledger.account(a.address)
    with pytest.raises(Exception):
        account.balance = 1000000
    assert ledger.balance(a.address) == 100
