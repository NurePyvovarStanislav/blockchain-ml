"""From repository PZ1/: python demo_step2.py"""

from dataclasses import replace
from blockchain.mempool import Mempool
from blockchain.state import LedgerState
from blockchain.transaction import create_signed_transaction
from blockchain.wallet import Wallet


def show(label: str, result) -> None:
    print(f"{label:<24} accepted={result.accepted!s:<5} code={result.code}")
    if not result.accepted:
        print("  reason:", result.reason)


def main() -> None:
    alice, bob, charlie = Wallet.generate(), Wallet.generate(), Wallet.generate()

    # Demo-only trusted initial state, not a public minting endpoint.
    # Later genesis/block validation (Steps 3-5) replaces this fixture.
    ledger = LedgerState({alice.address: 100, bob.address: 0, charlie.address: 0})
    pool = Mempool()
    print("INITIAL BALANCES: Alice=100, Bob=0, Charlie=0")

    first = create_signed_transaction(alice, bob.address, amount=70, fee=2, nonce=0)
    show("TX1: Alice -> Bob 70", pool.add(first, ledger))
    print("Reserved by Alice:", pool.reserved(alice.address))
    print("Alice available:", pool.available_balance(alice.address, ledger))

    overspend = create_signed_transaction(alice, charlie.address, 30, 1, 1)
    show("TX2: over-spend", pool.add(overspend, ledger))

    wrong_nonce = create_signed_transaction(alice, charlie.address, 10, 1, 7)
    show("TX3: wrong nonce", pool.add(wrong_nonce, ledger))

    show("TX1 repeated", pool.add(first, ledger))
    show("TX1 amount tampered", pool.add(replace(first, amount=700), ledger))

    second = create_signed_transaction(alice, charlie.address, amount=20, fee=1, nonce=1)
    show("TX4: Alice -> Charlie 20", pool.add(second, ledger))
    print("Mempool size:", len(pool))
    print("Alice available:", pool.available_balance(alice.address, ledger))

    print("\nSIMULATED CONFIRMATION (blocks are introduced in Step 3):")
    show("Confirm TX1", ledger.apply_confirmed(first))
    dropped = pool.reconcile(ledger)
    print("Dropped pending after state change:", len(dropped))
    print("Confirmed Alice balance:", ledger.balance(alice.address))
    print("Confirmed Bob balance:", ledger.balance(bob.address))
    print("Unclaimed fees for future miner:", ledger.unclaimed_fees)
    print("Pending TXIDs:", [tx.txid[:12] for tx in pool.transactions()])
    show("TX1 repeat confirmed", pool.add(first, ledger))


if __name__ == "__main__":
    main()
