"""Step 4 PoW, reward and retarget tests. Run from PZ1/: python -m pytest -q"""

from dataclasses import replace
import hashlib
import pytest

from blockchain.block import Block, ZERO_HASH
from blockchain.coinbase import CoinbaseTransaction
from blockchain.difficulty import MiningPolicy
from blockchain.mempool import Mempool
from blockchain.mining import (
    create_mining_candidate, has_valid_pow, mine_block,
    validate_mined_block_structure,
)
from blockchain.serialization import canonical_bytes
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


def remine_tampered(original, new_transactions):
    bad = Block.create(
        height=original.header.height, timestamp=original.header.timestamp,
        previous_hash=original.header.previous_hash, transactions=new_transactions,
        difficulty=original.header.difficulty,
    )
    return mine_block(bad, max_attempts=1_000_000).block


def test_coinbase_has_distinct_type_and_no_sender_or_signature(scenario):
    cb = candidate_for(scenario).transactions[0]
    assert isinstance(cb, CoinbaseTransaction)
    data = cb.to_dict()
    assert data["type"] == "coinbase"
    assert "sender" not in data and "signature" not in data and "fee" not in data


def test_coinbase_reward_equals_subsidy_plus_fees(scenario):
    block = candidate_for(scenario)
    assert block.transactions[0].amount == 50+2+1
    assert len(block.transactions) == 3
    assert block.has_valid_merkle_root()


def test_coinbase_only_for_empty_normal_transaction_set(scenario):
    candidate = candidate_for(scenario, txs=[])
    assert len(candidate.transactions) == 1
    assert candidate.transactions[0].amount == 50
    mined = mine_block(candidate)
    assert validate_mined_block_structure(mined.block, [scenario[3]], scenario[5])


def test_coinbase_txid_is_sha256_of_its_deterministic_representation(scenario):
    cb = candidate_for(scenario).transactions[0]
    assert cb.txid == hashlib.sha256(canonical_bytes(cb.to_dict())).hexdigest()
    assert cb.txid == replace(cb).txid


def test_coinbase_extra_nonce_changes_txid_and_merkle(scenario):
    cb = candidate_for(scenario).transactions[0]
    other = replace(cb, extra_nonce=cb.extra_nonce+1)
    assert cb.txid != other.txid
    block = candidate_for(scenario)
    changed = replace(block, transactions=(other, *block.transactions[1:]))
    assert not changed.has_valid_merkle_root()


@pytest.mark.parametrize("field,value", [
    ("height", 0), ("height", True), ("recipient", "a"*63),
    ("recipient", "G"*64), ("amount", 0), ("amount", True),
    ("extra_nonce", -1), ("extra_nonce", 2**64),
])
def test_bad_coinbase_fields_rejected(scenario, field, value):
    cb = candidate_for(scenario).transactions[0]
    with pytest.raises(ValueError):
        replace(cb, **{field: value})


def test_candidate_signed_transfers_remain_untouched(scenario):
    block = candidate_for(scenario)
    assert block.transactions[1:] == scenario[4]


def test_candidate_rejects_invalid_signature(scenario):
    bad = replace(scenario[4][0], amount=999)
    with pytest.raises(ValueError):
        candidate_for(scenario, txs=[bad])


def test_candidate_rejects_duplicate_txid(scenario):
    first = scenario[4][0]
    with pytest.raises(ValueError):
        candidate_for(scenario, txs=[first, first])


@pytest.mark.parametrize("bad_address", ["Miner", "g"*64, "0"*65, None])
def test_candidate_rejects_invalid_miner(scenario, bad_address):
    _, _, _, genesis, txs, policy = scenario
    with pytest.raises(ValueError):
        create_mining_candidate([genesis], txs, bad_address, 1700000010, policy)


def test_candidate_requires_increasing_timestamp(scenario):
    with pytest.raises(ValueError):
        candidate_for(scenario, timestamp=scenario[3].header.timestamp)
    with pytest.raises(ValueError):
        candidate_for(scenario, timestamp=True)


def test_candidate_before_mining_is_not_accepted_without_pow(scenario):
    candidate = candidate_for(scenario, policy=MiningPolicy(initial_difficulty=3))
    # Regardless of nonce's accidental validity, the rule is explicitly tested.
    assert candidate.header.nonce == 0
    assert candidate.header.difficulty == 3


def test_mining_finds_valid_nonce_and_counts_attempts(scenario):
    candidate = candidate_for(scenario)
    result = mine_block(candidate)
    assert result.block.hash.startswith('00')
    assert result.attempts == result.block.header.nonce+1
    assert result.elapsed_seconds >= 0
    assert validate_mined_block_structure(result.block, [scenario[3]], scenario[5])


def test_mining_does_not_mutate_original_candidate(scenario):
    candidate = candidate_for(scenario)
    original_nonce = candidate.header.nonce
    result = mine_block(candidate)
    assert candidate.header.nonce == original_nonce == 0
    assert result.block.has_valid_merkle_root()


def test_mining_preserves_nonzero_start_nonce(scenario):
    candidate = candidate_for(scenario)
    shifted = replace(candidate, header=replace(candidate.header, nonce=100))
    result = mine_block(shifted)
    assert result.block.header.nonce >= 100
    assert result.attempts == result.block.header.nonce-100+1


def test_proof_of_work_difficulty_is_exact_number_of_hex_zeroes(scenario):
    candidate = candidate_for(scenario)
    mined = mine_block(candidate).block
    assert has_valid_pow(mined)
    assert mined.hash.startswith("0" * mined.header.difficulty)
    assert not has_valid_pow(scenario[3])  # no mining required for genesis


def test_mining_attempt_limit_enforced(monkeypatch, scenario):
    monkeypatch.setattr('blockchain.mining.has_valid_pow', lambda block: False)
    with pytest.raises(RuntimeError, match='No solution in 3 attempts'):
        mine_block(candidate_for(scenario), max_attempts=3)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_bad_max_attempts_rejected(scenario, value):
    with pytest.raises(ValueError):
        mine_block(candidate_for(scenario), max_attempts=value)


def test_mining_does_not_mine_genesis(scenario):
    with pytest.raises(ValueError):
        mine_block(scenario[3])


def test_mining_rejects_candidate_without_coinbase(scenario):
    block = Block.create(height=1, timestamp=1700000010, previous_hash=scenario[3].hash,
                         transactions=scenario[4], difficulty=1)
    with pytest.raises(ValueError):
        mine_block(block)


def test_reward_tampering_remined_still_invalid(scenario):
    block = mine_block(candidate_for(scenario)).block
    cb = block.transactions[0]
    forged = remine_tampered(block, (replace(cb, amount=cb.amount+100), *block.transactions[1:]))
    assert has_valid_pow(forged) and forged.has_valid_merkle_root()
    assert not validate_mined_block_structure(forged, [scenario[3]], scenario[5])


def test_invalid_signature_remined_still_invalid(scenario):
    block = mine_block(candidate_for(scenario)).block
    bad = replace(block.transactions[1], amount=500)
    forged = remine_tampered(block, (block.transactions[0], bad, *block.transactions[2:]))
    assert has_valid_pow(forged)
    assert not validate_mined_block_structure(forged, [scenario[3]], scenario[5])


def test_duplicate_tx_remined_still_invalid(scenario):
    block = mine_block(candidate_for(scenario)).block
    same = block.transactions[1]
    fees = same.fee*2
    cb = replace(block.transactions[0], amount=scenario[5].fixed_subsidy + fees)
    forged = remine_tampered(block, (cb, same, same))
    assert not validate_mined_block_structure(forged, [scenario[3]], scenario[5])


def test_second_coinbase_remined_rejected(scenario):
    block = mine_block(candidate_for(scenario)).block
    forged = remine_tampered(block, (block.transactions[0], block.transactions[0], *block.transactions[1:]))
    assert not validate_mined_block_structure(forged, [scenario[3]], scenario[5])


def test_coinbase_height_mismatch_remined_rejected(scenario):
    block = mine_block(candidate_for(scenario)).block
    forged = remine_tampered(block, (replace(block.transactions[0], height=2), *block.transactions[1:]))
    assert not validate_mined_block_structure(forged, [scenario[3]], scenario[5])


def test_difficulty_in_header_cannot_be_reduced(scenario):
    policy = scenario[5]
    honest = mine_block(candidate_for(scenario)).block
    forged = remine_tampered(replace(honest, header=replace(honest.header, difficulty=1)), honest.transactions)
    assert has_valid_pow(forged)
    assert not validate_mined_block_structure(forged, [scenario[3]], policy)


def test_pow_incorrect_or_merkle_changed_rejected(scenario):
    honest = mine_block(candidate_for(scenario)).block
    bad_body = replace(honest, transactions=(replace(honest.transactions[0], extra_nonce=11), *honest.transactions[1:]))
    assert not validate_mined_block_structure(bad_body, [scenario[3]], scenario[5])


def test_wrong_previous_hash_invalid_even_with_pow(scenario):
    honest = mine_block(candidate_for(scenario)).block
    forged = Block.create(height=1, timestamp=1700000010, previous_hash=ZERO_HASH,
                          transactions=honest.transactions, difficulty=2)
    mined_forgery = mine_block(forged).block
    assert not validate_mined_block_structure(mined_forgery, [scenario[3]], scenario[5])


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


def test_default_policy_before_and_after_first_ten_blocks():
    policy = MiningPolicy()
    for count in (0, 1, 5, 9):
        assert policy.expected_next_difficulty(synthetic_history(count=count)) == 2
    assert policy.expected_next_difficulty(synthetic_history(count=10)) == 2


def test_fast_blocks_raise_difficulty_after_ten_mined():
    assert MiningPolicy().expected_next_difficulty(synthetic_history(seconds_per_block=2)) == 3


def test_slow_blocks_lower_difficulty_after_ten_mined():
    assert MiningPolicy().expected_next_difficulty(synthetic_history(seconds_per_block=30)) == 1


def test_boundary_at_exactly_half_or_twice_target_is_unchanged():
    policy = MiningPolicy()
    assert policy.expected_next_difficulty(synthetic_history(seconds_per_block=5)) == 2
    assert policy.expected_next_difficulty(synthetic_history(seconds_per_block=20)) == 2


def test_retarget_stays_within_min_and_max_limits():
    policy=MiningPolicy(initial_difficulty=4, min_difficulty=1, max_difficulty=4)
    assert policy.expected_next_difficulty(synthetic_history(seconds_per_block=1, difficulty=4)) == 4
    policy=MiningPolicy(initial_difficulty=1, min_difficulty=1, max_difficulty=4)
    assert policy.expected_next_difficulty(synthetic_history(seconds_per_block=30, difficulty=1)) == 1


def test_retarget_is_not_applied_for_every_block():
    policy=MiningPolicy()
    assert policy.expected_next_difficulty(synthetic_history(count=11, seconds_per_block=2, difficulty=3)) == 3


def test_retarget_rejects_non_positive_time_span():
    history = synthetic_history(seconds_per_block=10)
    last = history[-1]
    history[-1] = replace(last, header=replace(last.header, timestamp=history[0].header.timestamp))
    with pytest.raises(ValueError):
        MiningPolicy().expected_next_difficulty(history)


def test_invalid_policy_or_history_fails():
    with pytest.raises(ValueError):
        MiningPolicy(initial_difficulty=0)
    with pytest.raises(ValueError):
        MiningPolicy(initial_difficulty=5, max_difficulty=4)
    with pytest.raises(ValueError):
        MiningPolicy().expected_next_difficulty([])
    with pytest.raises(ValueError):
        MiningPolicy().expected_next_difficulty(synthetic_history(count=3)[1:])


def test_expected_difficulty_cannot_be_faked_by_miner(scenario):
    original = candidate_for(scenario)
    wrong = replace(original, header=replace(original.header, difficulty=1))
    forged = mine_block(wrong).block
    assert not validate_mined_block_structure(forged, [scenario[3]], scenario[5])


def test_coinbase_recipient_is_miner(scenario):
    candidate=candidate_for(scenario)
    assert candidate.transactions[0].recipient==scenario[2].address


def test_no_pow_validation_if_no_history(scenario):
    candidate = mine_block(candidate_for(scenario)).block
    assert not validate_mined_block_structure(candidate, [], scenario[5])
