"""Run from PZ1/: python demo_step4.py

Step 4 shows PoW mining, exact coinbase reward and retarget policy.
Important: mining a candidate does NOT yet confirm balances (Step 5).
"""

from dataclasses import replace

from blockchain.block import Block, ZERO_HASH
from blockchain.difficulty import MiningPolicy
from blockchain.mempool import Mempool
from blockchain.mining import (
    create_mining_candidate, has_valid_pow,
    mine_block, validate_mined_block_structure,
)
from blockchain.state import LedgerState
from blockchain.transaction import create_signed_transaction
from blockchain.wallet import Wallet


def timing_history(seconds_per_block: int, policy: MiningPolicy) -> list[Block]:
    """Create 10 SYNTHETIC header timestamps to show the retarget formula.

    These are deliberately NOT mined or accepted into a real chain. This
    isolates the deterministic timing formula from randomness of actual mining.
    """
    genesis = Block.create(
        height=0, timestamp=1_700_000_000,
        previous_hash=ZERO_HASH, transactions=[], difficulty=0,
    )
    history = [genesis]
    for height in range(1, policy.retarget_interval + 1):
        history.append(Block.create(
            height=height,
            timestamp=genesis.header.timestamp + height * seconds_per_block,
            previous_hash=history[-1].hash,
            transactions=[], difficulty=policy.initial_difficulty,
        ))
    return history


def main() -> None:
    alice, bob, miner = Wallet.generate(), Wallet.generate(), Wallet.generate()
    policy = MiningPolicy(
        fixed_subsidy=50, initial_difficulty=2,
        target_block_seconds=10, retarget_interval=10,
        min_difficulty=1, max_difficulty=4,
    )
    genesis = Block.create(
        height=0, timestamp=1_700_000_000,
        previous_hash=ZERO_HASH, transactions=[], difficulty=0,
    )
    history = [genesis]
    ledger = LedgerState({alice.address: 100, bob.address: 0})
    pool = Mempool()
    tx1 = create_signed_transaction(alice, bob.address, 40, 2, 0)
    tx2 = create_signed_transaction(alice, bob.address, 10, 1, 1)
    assert pool.add(tx1, ledger).accepted
    assert pool.add(tx2, ledger).accepted

    candidate = create_mining_candidate(
        history, pool.transactions(), miner.address,
        timestamp=genesis.header.timestamp + 10, policy=policy,
    )
    reward = candidate.transactions[0]
    print("STEP 4: PROOF OF WORK, COINBASE, RETARGET")
    print("Mempool transactions:", len(pool))
    print("Mining difficulty:", candidate.header.difficulty)
    print("Fixed subsidy:", policy.fixed_subsidy)
    print("Transaction fees:", tx1.fee + tx2.fee)
    print("Coinbase reward:", reward.amount)
    print("First transaction is coinbase:", candidate.transactions[0] is reward)

    result = mine_block(candidate, max_attempts=1_000_000)
    mined = result.block
    print("\nMINING RESULT:")
    print("Block hash:", mined.hash)
    print("Nonce found:", mined.header.nonce)
    print("Hash attempts:", result.attempts)
    print("Elapsed seconds:", f"{result.elapsed_seconds:.6f}")
    print("Proof of Work valid:", has_valid_pow(mined))
    print("Block reward / structure valid:",
          validate_mined_block_structure(mined, history, policy))
    print("Block linked to genesis:", mined.is_linked_to(genesis))
    print("Merkle Root valid:", mined.has_valid_merkle_root())

    # Prove that finding SHA-256 PoW cannot authorize an inflated coinbase.
    forged_coinbase = replace(reward, amount=reward.amount + 100)
    forged_candidate = Block.create(
        height=1, timestamp=candidate.header.timestamp,
        previous_hash=genesis.hash,
        transactions=(forged_coinbase, tx1, tx2),
        difficulty=policy.initial_difficulty,
    )
    forged = mine_block(forged_candidate).block
    print("\nATTEMPT TO OVERPAY THE MINER:")
    print("Forged block has valid PoW:", has_valid_pow(forged))
    print("Forged block is accepted:",
          validate_mined_block_structure(forged, history, policy))

    print("\nDIFFICULTY RETARGET (synthetic timestamps; NOT mined blocks):")
    for label, interval in (("fast", 2), ("normal", 10), ("slow", 30)):
        synthetic = timing_history(interval, policy)
        print(f"{label:6s}: {interval:2d} s/block -> "
              f"next difficulty {policy.expected_next_difficulty(synthetic)}")

    print("\nNO STATE CONFIRMATION IN STEP 4:")
    print("Confirmed Alice balance:", ledger.balance(alice.address))
    print("Confirmed Bob balance:", ledger.balance(bob.address))
    print("Confirmed miner balance:", ledger.balance(miner.address))
    print("Pending transactions still in mempool:", len(pool))
    print("NOTICE: Step 5 will validate the full chain and apply balances / miner rewards.")


if __name__ == "__main__":
    main()
