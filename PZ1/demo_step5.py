"""Run from PZ1: python demo_step5.py. Timestamps are synthetic for a short demo."""
import argparse
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from blockchain.coinbase import CoinbaseTransaction

from blockchain.chain import Blockchain, POLICY, GENESIS_TIMESTAMP, validate_chain
from blockchain.mining import create_mining_candidate, mine_block
from blockchain.transaction import create_signed_transaction
from blockchain.wallet import Wallet


def main(save_path=None):
    alice, bob, miner = Wallet.generate(), Wallet.generate(), Wallet.generate()
    chain = Blockchain()
    print("STEP 5: CHAIN, STATE REPLAY AND PERSISTENCE")
    print("Genesis identical on independent nodes:", chain.tip.hash == Blockchain().tip.hash)
    print("Initial Alice balance:", chain.balance(alice.address))
    for i in (1, 2):
        chain.mine_pending(alice.address, timestamp=GENESIS_TIMESTAMP + i * 10)
    print("Alice balance after two subsidies:", chain.balance(alice.address))
    tx1 = create_signed_transaction(alice, bob.address, 40, 2, 0)
    tx2 = create_signed_transaction(alice, bob.address, 10, 1, 1)
    print("TX1 admission:", chain.submit_transaction(tx1).code)
    print("TX2 admission:", chain.submit_transaction(tx2).code)
    print("Before acceptance: Alice=", chain.balance(alice.address), ", pending=", len(chain.pending()), sep="")
    chain.mine_pending(miner.address, timestamp=GENESIS_TIMESTAMP + 30)
    print("After block 3: Alice=", chain.balance(alice.address),
          ", Bob=", chain.balance(bob.address), ", Miner=", chain.balance(miner.address), sep="")
    print("Alice next nonce:", chain.ledger.next_nonce(alice.address))
    print("Unclaimed fees:", chain.ledger.unclaimed_fees)
    print("Pending transactions:", len(chain.pending()))
    print("Repeated confirmed TX1:", chain.submit_transaction(tx1).code)
    replay_nonce = create_signed_transaction(alice, bob.address, 1, 0, 0)
    print("New signed TX with old nonce:", chain.submit_transaction(replay_nonce).code)
    chain.mine_pending(miner.address, timestamp=GENESIS_TIMESTAMP + 40)
    print("Whole chain:", chain.validate().code)
    print("Tip hash:", chain.tip.hash)
    print("Total supply after four mined blocks:", sum(chain.balance(w.address) for w in (alice, bob, miner)))

    # Persist only block history, then reconstruct all balances from it.
    with TemporaryDirectory() as directory:
        path = Path(directory) / "chain.json"
        chain.save(path)
        restored = Blockchain.load(path)
        print("JSON restored: same tip:", restored.tip.hash == chain.tip.hash)
        print("JSON restored balances:", [restored.balance(w.address) for w in (alice, bob, miner)])
        print("JSON restored nonce:", restored.ledger.next_nonce(alice.address))
        print("JSON restored chain:", restored.validate().code)

    if save_path is not None:
        chain.save(save_path)
        print("Saved persistent chain:", save_path)

    print("\nREJECTION SCENARIOS:")
    # First, a transaction modified without its owner's signature.
    old = chain.blocks[3]
    changed = replace(tx1, amount=999)
    tampered = replace(old, transactions=(old.transactions[0], changed, tx2))
    print("Edited old transfer:", validate_chain((*chain.blocks[:3], tampered, chain.blocks[4])).code)
    # Then an authorized but different transfer and a newly mined header.
    # This isolates the successor link check instead of failing a signature first.
    altered = create_signed_transaction(alice, bob.address, 41, 2, 0)
    rebuilt = mine_block(create_mining_candidate(chain.blocks[:3], [altered, tx2],
                          miner.address, old.header.timestamp, POLICY)).block
    print("Re-mined block with original successor:", validate_chain((*chain.blocks[:3], rebuilt, chain.blocks[4])).code)
    # A valid signature and PoW are not enough if funds are insufficient.
    overspend = create_signed_transaction(alice, bob.address, 999, 0, 2)
    candidate = create_mining_candidate(chain.blocks, [overspend], miner.address,
                                         GENESIS_TIMESTAMP + 50, POLICY)
    invalid = mine_block(candidate).block
    print("Mined overspend:", chain.add_block(invalid).code)
    print("Original state unchanged:", chain.balance(alice.address) == 47 and chain.tip.header.height == 4)
    print("Original chain remains valid:", chain.validate().accepted)


def restore(path):
    chain = Blockchain.load(path)
    addresses = set()
    for block in chain.blocks:
        for tx in block.transactions:
            addresses.add(tx.recipient) # type: ignore
            if not isinstance(tx, CoinbaseTransaction): # type: ignore
                addresses.add(tx.sender) # type: ignore
    print("RESTORED IN A NEW PROCESS")
    print("Height:", chain.tip.header.height)
    print("Tip hash:", chain.tip.hash)
    print("Validation:", chain.validate().code)
    for address in sorted(addresses):
        print(address, "balance=", chain.balance(address),
              "next_nonce=", chain.ledger.next_nonce(address))
    print("Total supply:", sum(chain.balance(address) for address in addresses))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--save", type=Path, help="Save the demonstration chain to this file")
    mode.add_argument("--restore", type=Path, help="Restore a previously saved demonstration chain")
    args = parser.parse_args()
    if args.restore is not None:
        restore(args.restore)
    else:
        main(args.save)
