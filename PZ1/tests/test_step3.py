"""Step 3 tests. No mining, balances or full-chain consensus is claimed."""

from dataclasses import replace
import hashlib

import pytest

from blockchain.block import Block, ZERO_HASH
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


def test_two_leaves_merkle_root_manual(sample_transactions):
    a, b = [leaf_hash(tx.txid) for tx in sample_transactions[:2]]
    assert merkle_root([tx.txid for tx in sample_transactions[:2]]) == parent_hash(a, b).hex()


def test_three_leaves_duplicate_last_at_odd_level(sample_transactions):
    a, b, c = [leaf_hash(tx.txid) for tx in sample_transactions[:3]]
    expected = parent_hash(parent_hash(a, b), parent_hash(c, c))
    assert merkle_root([tx.txid for tx in sample_transactions[:3]]) == expected.hex()
    a, b, c, d, e = [leaf_hash(tx.txid) for tx in sample_transactions]
    ab, cd, ee = parent_hash(a, b), parent_hash(c, d), parent_hash(e, e)
    expected5 = parent_hash(parent_hash(ab, cd), parent_hash(ee, ee))
    assert merkle_root([tx.txid for tx in sample_transactions]) == expected5.hex()

def test_empty_and_single_leaf_roots(sample_transactions):
    assert merkle_root([]) == hashlib.sha256(b"\x02").hexdigest()
    txid = sample_transactions[0].txid
    assert merkle_root([txid]) == leaf_hash(txid).hex()


def test_header_fields_hash_and_canonical_serialization(sample_transactions):
    block = new_block(sample_transactions[:3])
    header = block.header
    assert set(header.to_dict()) == {"height", "timestamp", "previous_hash", "merkle_root", "difficulty", "nonce"}
    assert header.to_bytes() == canonical_bytes(header.to_dict())
    assert block.hash == hashlib.sha256(header.to_bytes()).hexdigest()
    assert block.hash == new_block(sample_transactions[:3]).hash
    assert block.has_valid_merkle_root()
    assert block.header.merkle_root == merkle_root([tx.txid for tx in block.transactions])
    assert replace(header, nonce=header.nonce + 1).hash != block.hash


def test_merkle_detects_changed_data_and_order(sample_transactions):
    block = new_block(sample_transactions[:3])
    ids = [t.txid for t in block.transactions]
    assert merkle_root(ids) != merkle_root([ids[1], ids[0], ids[2]])
    changed = replace(block.transactions[0], amount=777)
    tampered = replace(block, transactions=(changed, *block.transactions[1:]))
    assert not tampered.has_valid_merkle_root() and tampered.hash == block.hash
