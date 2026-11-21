"""Step 3 tests. No mining, balances or full-chain consensus is claimed."""

from dataclasses import replace
import hashlib
import json

import pytest

from blockchain.block import Block, BlockHeader, ZERO_HASH
from blockchain.merkle import leaf_hash, merkle_root, parent_hash
from blockchain.serialization import canonical_bytes
from blockchain.transaction import create_signed_transaction
from blockchain.wallet import Wallet


@pytest.fixture
def sample_transactions():
    alice, bob, charlie = Wallet.generate(), Wallet.generate(), Wallet.generate()
    return [
        create_signed_transaction(alice, bob.address, 10, 1, 0),
        create_signed_transaction(alice, charlie.address, 20, 2, 1),
        create_signed_transaction(alice, bob.address, 5, 0, 2),
        create_signed_transaction(alice, charlie.address, 3, 1, 3),
        create_signed_transaction(alice, bob.address, 2, 1, 4),
    ]


def new_block(transactions, *, height=1, previous_hash=ZERO_HASH,
              difficulty=2, nonce=0, timestamp=1_700_000_001):
    return Block.create(
        height=height, timestamp=timestamp, previous_hash=previous_hash,
        transactions=transactions, difficulty=difficulty, nonce=nonce,
    )


def test_empty_merkle_root_is_defined_and_deterministic():
    assert merkle_root([]) == hashlib.sha256(b"\x02").hexdigest()


def test_single_transaction_merkle_root(sample_transactions):
    assert merkle_root([sample_transactions[0].txid]) == leaf_hash(sample_transactions[0].txid).hex()


def test_two_leaves_merkle_root_manual(sample_transactions):
    a, b = [leaf_hash(tx.txid) for tx in sample_transactions[:2]]
    assert merkle_root([tx.txid for tx in sample_transactions[:2]]) == parent_hash(a, b).hex()


def test_three_leaves_duplicate_last_at_odd_level(sample_transactions):
    a, b, c = [leaf_hash(tx.txid) for tx in sample_transactions[:3]]
    expected = parent_hash(parent_hash(a, b), parent_hash(c, c))
    assert merkle_root([tx.txid for tx in sample_transactions[:3]]) == expected.hex()


def test_five_leaves_duplicate_odd_levels(sample_transactions):
    a, b, c, d, e = [leaf_hash(tx.txid) for tx in sample_transactions]
    ab, cd, ee = parent_hash(a, b), parent_hash(c, d), parent_hash(e, e)
    abcd, eeee = parent_hash(ab, cd), parent_hash(ee, ee)
    assert merkle_root([tx.txid for tx in sample_transactions]) == parent_hash(abcd, eeee).hex()


def test_merkle_root_order_matters(sample_transactions):
    ids = [tx.txid for tx in sample_transactions[:3]]
    assert merkle_root(ids) != merkle_root([ids[1], ids[0], ids[2]])


def test_merkle_root_changes_with_transaction_data(sample_transactions):
    first = sample_transactions[0]
    altered = replace(first, amount=999)
    assert first.txid != altered.txid
    assert merkle_root([first.txid]) != merkle_root([altered.txid])


@pytest.mark.parametrize("invalid", ["a" * 63, "g" * 64, "AB" * 32, None, 123])
def test_merkle_rejects_invalid_txid(invalid):
    with pytest.raises(ValueError):
        merkle_root([invalid])


def test_merkle_rejects_str_as_collection():
    with pytest.raises(TypeError):
        merkle_root("a" * 64)


def test_parent_rejects_wrong_length():
    with pytest.raises(ValueError):
        parent_hash(b"\x00" * 31, b"\x00" * 32)


def test_block_contains_all_required_header_fields(sample_transactions):
    block = new_block(sample_transactions[:3])
    assert set(block.header.to_dict()) == {
        "height", "timestamp", "previous_hash", "merkle_root", "difficulty", "nonce"
    }
    assert len(block.hash) == 64
    assert block.has_valid_merkle_root()
    assert len(block.transactions) == 3
    assert block.header.merkle_root == merkle_root([tx.txid for tx in sample_transactions[:3]])


def test_header_hash_is_sha256_of_serialized_header(sample_transactions):
    header = new_block(sample_transactions[:1]).header
    assert header.hash == hashlib.sha256(canonical_bytes(header.to_dict())).hexdigest()
    assert header.to_bytes() == canonical_bytes(header.to_dict())


def test_header_serialization_reproducible(sample_transactions):
    a = new_block(sample_transactions[:1])
    b = new_block(sample_transactions[:1])
    assert a.header.to_bytes() == b.header.to_bytes()
    assert a.hash == b.hash


def test_block_tampering_breaks_merkle_check(sample_transactions):
    block = new_block(sample_transactions[:3])
    changed = replace(sample_transactions[0], amount=777)
    tampered = replace(block, transactions=(changed, *block.transactions[1:]))
    assert not tampered.has_valid_merkle_root()
    assert tampered.hash == block.hash  # hash is of the unchanged header


def test_header_rebuild_breaks_successor_link(sample_transactions):
    first = new_block(sample_transactions[:3])
    next_block = new_block([], height=2, previous_hash=first.hash, timestamp=1_700_000_002)
    changed = replace(sample_transactions[0], amount=777)
    rebuilt = new_block([changed, *sample_transactions[1:3]])
    assert rebuilt.has_valid_merkle_root()
    assert rebuilt.hash != first.hash
    assert not next_block.is_linked_to(rebuilt)
    assert next_block.is_linked_to(first)


def test_genesis_like_block_empty_transactions():
    genesis = new_block([], height=0, difficulty=0, nonce=0)
    assert genesis.has_valid_merkle_root()
    assert genesis.header.previous_hash == ZERO_HASH


def test_nonce_change_changes_header_hash(sample_transactions):
    block = new_block(sample_transactions[:1])
    changed = replace(block.header, nonce=block.header.nonce + 1)
    assert changed.hash != block.hash


def test_difficulty_change_changes_header_hash(sample_transactions):
    block = new_block(sample_transactions[:1])
    changed = replace(block.header, difficulty=block.header.difficulty + 1)
    assert changed.hash != block.hash


def test_transaction_is_immutable_sequence(sample_transactions):
    values = sample_transactions[:2]
    block = new_block(values)
    values.append(sample_transactions[2])
    assert len(block.transactions) == 2
    assert isinstance(block.transactions, tuple)


def test_block_json_representation(sample_transactions):
    block = new_block(sample_transactions[:1])
    representation = block.to_dict()
    assert representation["hash"] == block.hash
    assert representation["transactions"][0] == sample_transactions[0].to_dict()
    assert json.loads(json.dumps(representation))["header"] == block.header.to_dict()


@pytest.mark.parametrize("field,value", [
    ("height", -1), ("height", True), ("timestamp", -1),
    ("previous_hash", "nope"), ("merkle_root", "0" * 63),
    ("difficulty", -1), ("difficulty", 65), ("difficulty", True),
    ("nonce", -1), ("nonce", 2**64),
])
def test_invalid_header_rejected(field, value, sample_transactions):
    data = new_block(sample_transactions[:1]).header.to_dict()
    data[field] = value
    with pytest.raises(ValueError):
        BlockHeader(**data)


def test_wrong_block_transaction_type_rejected(sample_transactions):
    header = new_block(sample_transactions[:1]).header
    with pytest.raises(TypeError):
        Block(header=header, transactions=(object(),))  # type: ignore[arg-type]


def test_forged_txid_not_automatically_signed(sample_transactions):
    # Structural validation intentionally does not replace signature checks.
    original = sample_transactions[0]
    altered = replace(original, fee=999)
    block = new_block([altered])
    assert block.has_valid_merkle_root()
    assert not altered.verify_signature()
