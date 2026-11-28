"""Step 4 PoW, reward and retarget tests. Run from PZ1/: python -m pytest -q"""

import pytest

from blockchain.block import Block, ZERO_HASH
from blockchain.coinbase import CoinbaseTransaction
from blockchain.difficulty import MiningPolicy
from blockchain.mempool import Mempool
from blockchain.mining import (
    create_mining_candidate, mine_block,
    validate_mined_block_structure,
)
from blockchain.state import LedgerState
from blockchain.transaction import create_signed_transaction
from blockchain.wallet import Wallet


@pytest.fixture
def scenario():
    alice, bob, miner = Wallet.generate(), Wallet.generate(), Wallet.generate()
    genesis = Block.create(height=0, timestamp=1_700_000_000, previous_hash=ZERO_HASH,
                           transactions=(), difficulty=0)
    tx0 = create_signed_transaction(alice, bob.address, 40, 2, 0)
    tx1 = create_signed_transaction(alice, bob.address, 10, 1, 1)
    return alice, bob, miner, genesis, (tx0, tx1), MiningPolicy()


def candidate_for(scenario, *, txs=None, timestamp=None, policy=None):
    a, b, miner, genesis, originals, original_policy = scenario
    return create_mining_candidate(
        [genesis], originals if txs is None else txs, miner.address,
        genesis.header.timestamp + 10 if timestamp is None else timestamp,
        original_policy if policy is None else policy,
    )


def synthetic_history(*, seconds_per_block=10, count=10, difficulty=2):
    """Only a timing fixture: blocks are deliberately NOT mined or fully valid."""
    result = [Block.create(height=0, timestamp=10000, previous_hash=ZERO_HASH,
                           transactions=[], difficulty=0)]
    for height in range(1, count+1):
        result.append(Block.create(height=height, timestamp=10000+height*seconds_per_block,
                                   previous_hash=result[-1].hash, transactions=[],
                                   difficulty=difficulty))
    return result


def test_mining_finds_valid_nonce_and_counts_attempts(scenario):
    candidate = candidate_for(scenario)
    result = mine_block(candidate)
    assert result.block.hash.startswith('00')
    assert result.attempts == result.block.header.nonce+1
    assert result.elapsed_seconds >= 0
    assert validate_mined_block_structure(result.block, [scenario[3]], scenario[5])


def test_mined_block_does_not_prematurely_confirm_ledger(scenario):
    alice, bob, miner, genesis, txs, policy = scenario
    ledger = LedgerState({alice.address: 100, bob.address: 0})
    pool = Mempool()
    assert all(pool.add(tx, ledger).accepted for tx in txs)
    mined = mine_block(create_mining_candidate([genesis], pool.transactions(),
                                               miner.address, 1700000010, policy)).block
    assert validate_mined_block_structure(mined, [genesis], policy)
    assert ledger.balance(alice.address) == 100
    assert ledger.balance(bob.address) == 0
    assert ledger.balance(miner.address) == 0
    assert ledger.unclaimed_fees == 0
    assert len(pool) == 2


def test_retarget_stays_within_min_and_max_limits():
    policy=MiningPolicy(initial_difficulty=4, min_difficulty=1, max_difficulty=4)
    assert policy.expected_next_difficulty(synthetic_history(seconds_per_block=1, difficulty=4)) == 4
    policy=MiningPolicy(initial_difficulty=1, min_difficulty=1, max_difficulty=4)
    assert policy.expected_next_difficulty(synthetic_history(seconds_per_block=30, difficulty=1)) == 1

def test_coinbase_reward_and_fee_structure(scenario):
    block = candidate_for(scenario)
    reward = block.transactions[0]
    assert isinstance(reward, CoinbaseTransaction)
    assert reward.amount == 53 and reward.recipient == scenario[2].address
    assert not {"sender", "signature", "fee"} & reward.to_dict().keys()
    assert block.transactions[1:] == scenario[4] and block.has_valid_merkle_root()
    empty = candidate_for(scenario, txs=[])
    assert len(empty.transactions) == 1 and empty.transactions[0].amount == 50


def test_retarget_only_at_interval(scenario):
    policy = MiningPolicy()
    for count in (0, 1, 5, 9):
        assert policy.expected_next_difficulty(synthetic_history(count=count)) == 2
    assert policy.expected_next_difficulty(synthetic_history(count=10)) == 2
    assert policy.expected_next_difficulty(synthetic_history(count=11, difficulty=3)) == 3


def test_retarget_fast_slow_and_thresholds():
    policy = MiningPolicy()
    for seconds, expected in ((2, 3), (5, 2), (20, 2), (30, 1)):
        assert policy.expected_next_difficulty(synthetic_history(seconds_per_block=seconds)) == expected
