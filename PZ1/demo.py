"""Run: python demo.py"""

from dataclasses import replace
import json

from blockchain.wallet import Wallet
from blockchain.transaction import create_signed_transaction


def main() -> None:
    alice = Wallet.generate()
    bob = Wallet.generate()

    tx = create_signed_transaction(
        sender_wallet=alice,
        recipient=bob.address,
        amount=100,
        fee=2,
        nonce=0,
    )

    print("Alice address:", alice.address)
    print("Bob address:  ", bob.address)
    print("Signed transaction:")
    print(json.dumps(tx.to_dict(), indent=2, ensure_ascii=False))
    print("TXID:", tx.txid)
    print("Signature valid:", tx.verify_signature())

    forged = replace(tx, amount=1000)
    print("Changed amount, signature valid:", forged.verify_signature())

    # This is not a transfer of money yet: balance/nonce checks are step 2.
    print("Balances were not changed: step 1 does not contain a ledger.")


if __name__ == "__main__":
    main()
