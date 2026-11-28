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

def test_wallet_address_and_valid_signature(example):
    import hashlib
    alice, _, tx = example
    assert len(alice.public_key_bytes) == 32
    assert alice.address == hashlib.sha256(alice.public_key_bytes).hexdigest()
    assert tx.network == NETWORK_ID and tx.verify_signature()


def test_deterministic_serialization_and_signature(example):
    alice, bob, tx = example
    assert canonical_bytes({"amount": 7, "from": "Alice"}) == canonical_bytes({"from": "Alice", "amount": 7})
    assert "signature" not in tx.unsigned_payload()
    identical = create_signed_transaction(alice, bob.address, 100, 2, 0)
    restored = Transaction.from_dict(tx.to_dict())
    assert identical.signature == tx.signature and identical.txid == tx.txid
    assert restored.verify_signature() and restored.txid == tx.txid


def test_signed_fields_and_key_tampering(example):
    _, _, tx = example
    for changed in ({"amount": 101}, {"fee": 3}, {"nonce": 1},
                    {"recipient": "0"*64}, {"network": "OTHER"},
                    {"sender": "0"*64}, {"signature": "0"*128},
                    {"public_key": "0"*64}):
        assert not replace(tx, **changed).verify_signature(), changed


def test_invalid_transaction_values(example):
    alice, bob, tx = example
    for amount in (-1, True):
        with pytest.raises(ValueError):
            create_signed_transaction(alice, bob.address, amount, 0, 0)
    with pytest.raises(ValueError):
        create_signed_transaction(alice, bob.address, 1, 0, 0, network="OTHER")
    with pytest.raises(ValueError):
        Transaction.from_dict(dict(tx.to_dict(), unexpected=10))
    assert create_signed_transaction(alice, bob.address, 1, 0, 1).verify_signature()


def test_encrypted_wallet_roundtrip_and_no_overwrite(example, tmp_path):
    alice, _, _ = example
    path = tmp_path / "alice.pem"
    password = "test-only-password"
    alice.save_encrypted(path, password)
    original = path.read_bytes()
    assert b"BEGIN ENCRYPTED PRIVATE KEY" in original
    assert Wallet.load_encrypted(path, password).address == alice.address
    with pytest.raises(ValueError):
        Wallet.load_encrypted(path, "incorrect")
    with pytest.raises(FileExistsError):
        alice.save_encrypted(path, password)
    assert path.read_bytes() == original
