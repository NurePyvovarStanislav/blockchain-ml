"""End-to-end Step 5 tests: state transitions, consensus and JSON storage."""
from dataclasses import replace
import json
from time import time

import pytest

from blockchain.block import Block
from blockchain.chain import (Blockchain, ChainValidationError, POLICY, GENESIS_TIMESTAMP,
                              create_genesis, validate_chain)
from blockchain.mining import create_mining_candidate, has_valid_pow, mine_block
from blockchain.transaction import create_signed_transaction
from blockchain.wallet import Wallet


@pytest.fixture
def funded():
    alice, bob, miner = (Wallet.generate() for _ in range(3))
    chain = Blockchain()
    chain.mine_pending(alice.address, timestamp=GENESIS_TIMESTAMP + 10)
    chain.mine_pending(alice.address, timestamp=GENESIS_TIMESTAMP + 20)
    return chain, alice, bob, miner


def mined(chain, miner, txs=(), *, timestamp=None):
    return mine_block(create_mining_candidate(chain.blocks, txs, miner.address,
        chain.tip.header.timestamp + 10 if timestamp is None else timestamp, POLICY)).block


def tx(alice, bob, amount=40, fee=2, nonce=0):
    return create_signed_transaction(alice, bob.address, amount, fee, nonce)


def funded_value():
    return Blockchain(), Wallet.generate(), Wallet.generate(), Wallet.generate()


def test_confirm_and_pay_reward(funded):
    c, a, b, m = funded
    first, second = tx(a,b), tx(a,b,10,1,1)
    assert c.submit_transaction(first).accepted and c.submit_transaction(second).accepted
    assert c.balance(a.address) == 100
    result = c.mine_pending(m.address, timestamp=GENESIS_TIMESTAMP + 30)
    assert result.block.transactions[0].amount == 53
    assert [c.balance(w.address) for w in (a,b,m)] == [47,50,53]
    assert c.ledger.next_nonce(a.address) == 2 and c.ledger.unclaimed_fees == 0
    assert c.ledger.was_confirmed(first.txid) and c.ledger.was_confirmed(result.block.transactions[0].txid)
    assert c.pending() == () and c.validate().accepted


def test_partial_state_rolled_back_on_second_transfer(funded):
    c,a,b,m = funded
    good = tx(a,b,60,1)
    bad = tx(a,b,50,1,1)
    c.submit_transaction(good)
    old = c.blocks
    assert c.add_block(mined(c,m,[good,bad])).code == 'insufficient_funds'
    assert c.blocks == old and c.balance(a.address) == 100 and c.balance(b.address) == 0
    assert c.ledger.next_nonce(a.address) == 0 and c.ledger.unclaimed_fees == 0
    assert not c.ledger.was_confirmed(good.txid) and c.pending() == (good,)


def test_wrong_parent_or_height(funded):
    c,a,b,m = funded
    block = mined(c,m)
    for header in [replace(block.header, previous_hash='a'*64), replace(block.header,height=99)]:
        assert c.add_block(replace(block, header=header)).code == 'invalid_link'


def test_wrong_difficulty_even_with_pow(funded):
    c,a,b,m = funded
    block = mined(c,m)
    easier = mine_block(replace(block,header=replace(block.header,difficulty=1,nonce=0))).block
    assert has_valid_pow(easier) and c.add_block(easier).code == 'invalid_difficulty'


def test_invalid_pow(funded):
    c,a,b,m = funded
    block = mined(c,m)
    nonce = 0
    while has_valid_pow(replace(block,header=replace(block.header,nonce=nonce))):
        nonce += 1
    assert c.add_block(replace(block,header=replace(block.header,nonce=nonce))).code == 'invalid_pow'


def test_future_and_nonincreasing_timestamps(funded):
    c,a,b,m = funded
    future = mined(c,m,timestamp=int(time())+1000)
    assert c.add_block(future).code == 'invalid_timestamp'
    valid = mined(c,m)
    assert c.add_block(replace(valid,header=replace(valid.header,timestamp=c.tip.header.timestamp))).code == 'invalid_timestamp'


def test_invalid_signature_and_merkle(funded):
    c,a,b,m = funded
    original = tx(a,b)
    block = mined(c,m,[original])
    forged = replace(block,transactions=(block.transactions[0],replace(original,amount=999)))
    assert c.add_block(forged).code == 'invalid_signature'
    signed_different = tx(a,b,41)
    changed = replace(block,transactions=(block.transactions[0],signed_different))
    assert c.add_block(changed).code == 'invalid_merkle'


def test_duplicate_body_rejected_even_if_merkle_ambiguous(funded):
    c,a,b,m = funded
    first,second = tx(a,b),tx(a,b,10,1,1)
    block = mined(c,m,[first,second])
    changed = replace(block,transactions=(*block.transactions,second))
    assert changed.has_valid_merkle_root()
    assert not c.add_block(changed).accepted


def test_save_and_load_roundtrip(funded,tmp_path):
    c,a,b,m = funded
    first = tx(a,b)
    c.submit_transaction(first); c.mine_pending(m.address,timestamp=GENESIS_TIMESTAMP+30)
    c.submit_transaction(tx(a,b,1,0,1))
    path=tmp_path/'nested'/'chain.json'; c.save(path)
    restored=Blockchain.load(path)
    assert restored.blocks == c.blocks and restored.balance(a.address) == 58
    assert restored.ledger.next_nonce(a.address) == 1 and restored.ledger.was_confirmed(first.txid)
    assert restored.pending() == () and restored.validate().accepted
    assert not list(path.parent.glob('*.tmp'))

def test_genesis_is_identical_and_altered_history_rejected():
    a, b = Blockchain(), Blockchain()
    assert a.tip == b.tip == create_genesis()
    assert a.balance("a"*64) == 0 and a.validate().accepted
    g = a.tip
    bad = replace(g, header=replace(g.header, timestamp=g.header.timestamp+1))
    assert validate_chain([bad]).code == "invalid_genesis"
    assert validate_chain([]).code == "invalid_genesis"


def test_block_nonce_and_confirmed_replay(funded):
    c,a,b,m = funded
    assert c.add_block(mined(c,m,[tx(a,b,1,0,2)])).code == "invalid_nonce"
    first = tx(a,b)
    assert c.add_block(mined(c,m,[first])).accepted
    assert c.add_block(mined(c,m,[first])).code == "duplicate"
    assert c.add_block(mined(c,m,[tx(a,b,1,0,0)])).code == "invalid_nonce"


def test_invalid_coinbase_even_with_valid_pow(funded):
    c,a,b,m = funded
    block = mined(c,m)
    cb = block.transactions[0]
    for body, code in (([replace(cb,amount=150)], "invalid_structure"),
                       ([replace(cb,height=99)], "invalid_structure"),
                       ([cb,replace(cb,extra_nonce=1)], "invalid_transaction_type")):
        bad = mine_block(Block.create(height=block.header.height,timestamp=block.header.timestamp,
                   previous_hash=c.tip.hash,transactions=body,difficulty=block.header.difficulty)).block
        assert has_valid_pow(bad)
        assert c.add_block(bad).code == code
        assert c.balance(m.address) == 0


def test_storage_detects_tampered_hash_and_transaction(funded, tmp_path):
    c,a,b,m = funded
    assert c.add_block(mined(c,m,[tx(a,b)])).accepted
    path = tmp_path / "chain.json"
    c.save(path)
    original = path.read_text()
    data = json.loads(original)
    data["blocks"][-1]["hash"] = "0"*64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        Blockchain.load(path)
    data = json.loads(original)
    data["blocks"][-1]["transactions"][1]["amount"] = 41
    path.write_text(json.dumps(data))
    with pytest.raises((ValueError, ChainValidationError)):
        Blockchain.load(path)
