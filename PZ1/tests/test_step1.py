from dataclasses import replace

import pytest

from blockchain.config import NETWORK_ID
from blockchain.serialization import canonical_bytes
from blockchain.transaction import Transaction, create_signed_transaction
from blockchain.wallet import Wallet


@pytest.fixture
def example():
    alice = Wallet.generate()
    bob = Wallet.generate()
    tx = create_signed_transaction(alice, bob.address, 100, 2, 0)
    return alice, bob, tx


def test_generated_public_key_is_32_bytes(example):
    alice, _, _ = example
    assert len(alice.public_key_bytes) == 32


def test_address_matches_sha256_public_key(example):
    import hashlib
    alice, _, _ = example
    assert alice.address == hashlib.sha256(alice.public_key_bytes).hexdigest()


def test_valid_signature(example):
    assert example[2].verify_signature()


@pytest.mark.parametrize("changed", [
    {"amount": 101},
    {"fee": 3},
    {"nonce": 1},
    {"recipient": "0" * 64},
    {"network": "ANOTHER-NETWORK"},
    {"sender": "0" * 64},
])
def test_tampered_fields_fail(example, changed):
    assert not replace(example[2], **changed).verify_signature()


def test_invalid_signature_fails(example):
    assert not replace(example[2], signature="0" * 128).verify_signature()


def test_invalid_public_key_fails(example):
    assert not replace(example[2], public_key="0" * 64).verify_signature()


def test_invalid_negative_amount_rejected(example):
    alice, bob, _ = example
    with pytest.raises(ValueError):
        create_signed_transaction(alice, bob.address, -1, 0, 0)


def test_boolean_amount_rejected(example):
    alice, bob, _ = example
    with pytest.raises(ValueError):
        create_signed_transaction(alice, bob.address, True, 0, 0)


def test_zero_fee_is_allowed(example):
    alice, bob, _ = example
    assert create_signed_transaction(alice, bob.address, 1, 0, 1).verify_signature()


def test_unsigned_json_is_deterministic():
    a = {"from": "Alice", "amount": 7}
    b = {"amount": 7, "from": "Alice"}
    assert canonical_bytes(a) == canonical_bytes(b)


def test_signature_not_in_payload(example):
    assert "signature" not in example[2].unsigned_payload()


def test_deterministic_signature_and_id(example):
    alice, bob, tx = example
    identical = create_signed_transaction(alice, bob.address, 100, 2, 0)
    assert tx.signature == identical.signature
    assert tx.txid == identical.txid


def test_signed_transaction_roundtrip(example):
    tx = example[2]
    restored = Transaction.from_dict(tx.to_dict())
    assert restored.verify_signature()
    assert restored.txid == tx.txid


def test_extra_field_rejected(example):
    tx = example[2].to_dict()
    tx["unexpected"] = 10
    with pytest.raises(ValueError):
        Transaction.from_dict(tx)


def test_encrypted_save_load(example, tmp_path):
    alice, _, _ = example
    path = tmp_path / "alice.pem"
    alice.save_encrypted(path, "correct horse battery staple")
    assert b"BEGIN ENCRYPTED PRIVATE KEY" in path.read_bytes()
    restored = Wallet.load_encrypted(path, "correct horse battery staple")
    assert restored.address == alice.address
    with pytest.raises(ValueError):
        Wallet.load_encrypted(path, "incorrect password")


def test_no_overwrite_existing_wallet(example, tmp_path):
    alice, _, _ = example
    path = tmp_path / "alice.pem"
    alice.save_encrypted(path, "correct horse battery staple")
    with pytest.raises(FileExistsError):
        alice.save_encrypted(path, "correct horse battery staple")


def test_unsupported_network_rejected(example):
    alice, bob, _ = example
    with pytest.raises(ValueError):
        create_signed_transaction(alice, bob.address, 100, 2, 0, network="OTHER")


def test_valid_transaction_network(example):
    assert example[2].network == NETWORK_ID
