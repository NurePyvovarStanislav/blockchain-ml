"""Run from PZ1/: python demo_step3.py

Demonstrates Step 3 *structure* without claiming mining or confirmation.
"""

from dataclasses import replace
import json

from blockchain.block import Block, ZERO_HASH
from blockchain.merkle import merkle_root
from blockchain.transaction import create_signed_transaction
from blockchain.wallet import Wallet


def main() -> None:
    alice, bob, charlie = Wallet.generate(), Wallet.generate(), Wallet.generate()

    tx0 = create_signed_transaction(alice, bob.address, 10, 1, 0)
    tx1 = create_signed_transaction(alice, charlie.address, 20, 1, 1)
    tx2 = create_signed_transaction(alice, bob.address, 5, 1, 2)

    genesis = Block.create(
        height=0, timestamp=1_700_000_000, previous_hash=ZERO_HASH,
        transactions=[], difficulty=0, nonce=0,
    )
    block1 = Block.create(
        height=1, timestamp=1_700_000_010, previous_hash=genesis.hash,
        transactions=[tx0, tx1, tx2], difficulty=2, nonce=0,
    )
    block2 = Block.create(
        height=2, timestamp=1_700_000_020, previous_hash=block1.hash,
        transactions=[], difficulty=2, nonce=0,
    )

    print("STEP 3: BLOCK AND MERKLE TREE (no mining yet)")
    print("Genesis hash:", genesis.hash)
    print("Block 1 hash:", block1.hash)
    print("Block 1 transactions:", len(block1.transactions))
    print("Block 1 Merkle root:", block1.header.merkle_root)
    print("Merkle root valid:", block1.has_valid_merkle_root())
    print("Block 1 linked to genesis:", block1.is_linked_to(genesis))
    print("Block 2 linked to Block 1:", block2.is_linked_to(block1))
    print("Merkle (2 TX):", merkle_root([tx0.txid, tx1.txid]))
    print("Merkle (3 TX):", merkle_root([tx0.txid, tx1.txid, tx2.txid]))

    print("\nBLOCK 1 HEADER:")
    print(json.dumps(block1.header.to_dict(), indent=2, ensure_ascii=False))

    # Change body, leave old root in header: root no longer matches.
    edited_tx = replace(tx0, amount=999)
    tampered = replace(block1, transactions=(edited_tx, tx1, tx2))
    print("\nOld header + changed transaction, Merkle valid:",
          tampered.has_valid_merkle_root())

    # Regenerate a new header: the following block still points to the old hash.
    rebuilt = Block.create(
        height=1, timestamp=block1.header.timestamp,
        previous_hash=genesis.hash, transactions=[edited_tx, tx1, tx2],
        difficulty=2, nonce=0,
    )
    print("Rebuilt Block 1 has a different hash:", rebuilt.hash != block1.hash)
    print("Block 2 linked to rebuilt Block 1:", block2.is_linked_to(rebuilt))
    print("\nNOTICE: Difficulty=2 is metadata only in Step 3; PoW is Step 4.")
    print("NOTICE: No balances, coinbase or full chain validation in this demo.")


if __name__ == "__main__":
    main()
