"""End-to-end Step 5 tests: state transitions, consensus and JSON storage."""
from dataclasses import replace
import json
from time import time

import pytest

from blockchain.block import Block
from blockchain.chain import (Blockchain, ChainValidationError, POLICY, GENESIS_TIMESTAMP,
                              create_genesis, replay_chain, validate_chain)
from blockchain.coinbase import CoinbaseTransaction
from blockchain.mining import create_mining_candidate, has_valid_pow, mine_block
from blockchain.storage import block_from_dict
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


def test_identical_genesis_and_zero_supply():
    a, b = Blockchain(), Blockchain()
    assert a.tip == b.tip == create_genesis()
    assert a.balance('a' * 64) == 0 and a.validate().accepted


def test_genesis_altered_rejected():
    g = create_genesis()
    assert validate_chain([replace(g, header=replace(g.header, timestamp=g.header.timestamp + 1))]).code == 'invalid_genesis'


def test_empty_chain_rejected():
    assert validate_chain([]).code == 'invalid_genesis'
    with pytest.raises(ChainValidationError):
        Blockchain([])


def test_mining_creates_money_from_zero():
    c, a, b, m = funded_value()
    assert c.balance(a.address) == 0
    c.mine_pending(a.address, timestamp=GENESIS_TIMESTAMP + 10)
    assert c.balance(a.address) == 50 and c.ledger.next_nonce(a.address) == 0


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


def test_external_block_not_required_in_mempool(funded):
    c,a,b,m = funded
    assert c.add_block(mined(c,m,[tx(a,b)])).accepted
    assert c.balance(b.address) == 40


def test_pending_conflict_removed_after_external_block(funded):
    c,a,b,m = funded
    assert c.submit_transaction(tx(a,b,70,1)).accepted
    assert c.add_block(mined(c,m,[tx(a,b,60,0)])).accepted
    assert c.pending() == () and c.balance(a.address) == 40


def test_unrelated_pending_survives_block(funded):
    c,a,b,m = funded
    first,second = tx(a,b),tx(a,b,10,1,1)
    c.submit_transaction(first); c.submit_transaction(second)
    assert c.add_block(mined(c,m,[first])).accepted
    assert c.pending() == (second,)


def test_overspend_with_valid_pow_rejected(funded):
    c,a,b,m = funded
    block = mined(c,m,[tx(a,b,101,0)])
    assert has_valid_pow(block)
    assert c.add_block(block).code == 'insufficient_funds'
    assert c.balance(a.address) == 100 and c.tip.header.height == 2


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


def test_invalid_nonce_rejected(funded):
    c,a,b,m = funded
    assert c.add_block(mined(c,m,[tx(a,b,1,0,2)])).code == 'invalid_nonce'


def test_confirmed_replay_and_different_tx_with_old_nonce(funded):
    c,a,b,m = funded
    first = tx(a,b)
    assert c.add_block(mined(c,m,[first])).accepted
    assert c.add_block(mined(c,m,[first])).code == 'duplicate'
    assert c.add_block(mined(c,m,[tx(a,b,1,0,0)])).code == 'invalid_nonce'


def test_in_block_incoming_transfer_can_fund_later_transfer(funded):
    c,a,b,m = funded
    assert c.add_block(mined(c,m,[tx(a,b,20,0),tx(b,a,19,1)])).accepted
    assert c.balance(b.address) == 0 and c.balance(a.address) == 99
    assert c.balance(m.address) == 51


def test_coinbase_cannot_fund_same_block():
    c,a,b,m = funded_value()
    assert c.add_block(mined(c,a,[tx(a,b,1,0)])).code == 'insufficient_funds'
    assert c.balance(a.address) == 0


def test_self_transfer_and_reward_conserve_supply(funded):
    c,a,b,m = funded
    assert c.add_block(mined(c,a,[tx(a,a,20,3)])).accepted
    assert c.balance(a.address) == 150 and c.ledger.next_nonce(a.address) == 1


def test_ledger_snapshot_cannot_mutate_chain(funded):
    c,a,b,m = funded
    snapshot = c.ledger
    assert snapshot.apply_confirmed(tx(a,b)).accepted
    assert c.balance(a.address) == 100 and c.balance(b.address) == 0


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


def test_overpaid_coinbase_with_valid_pow(funded):
    c,a,b,m = funded
    block = mined(c,m)
    reward = replace(block.transactions[0],amount=150)
    bad = mine_block(Block.create(height=block.header.height,timestamp=block.header.timestamp,
              previous_hash=c.tip.hash,transactions=[reward],difficulty=block.header.difficulty)).block
    assert c.add_block(bad).code == 'invalid_structure' and c.balance(m.address) == 0


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


def test_remined_old_block_breaks_successor_link(funded):
    c,a,b,m = funded
    original = mined(c,m,[tx(a,b)])
    assert c.add_block(original).accepted
    c.mine_pending(m.address,timestamp=GENESIS_TIMESTAMP+40)
    prefix = Blockchain(c.blocks[:3])
    rebuilt = mined(prefix,m,[tx(a,b,41)])
    assert validate_chain((*prefix.blocks,rebuilt)).accepted
    assert validate_chain((*prefix.blocks,rebuilt,c.tip)).code == 'invalid_link'


def test_replay_reconstructs_balances_and_nonces(funded):
    c,a,b,m = funded
    assert c.add_block(mined(c,m,[tx(a,b)])).accepted
    state = replay_chain(c.blocks)
    assert [state.balance(w.address) for w in (a,b,m)] == [58,40,52]
    assert state.next_nonce(a.address) == 1 and state.unclaimed_fees == 0


def test_retarget_replayed_from_valid_history():
    c = Blockchain(); a = Wallet.generate()
    for i in range(1,12):
        c.mine_pending(a.address,timestamp=GENESIS_TIMESTAMP+2*i)
    assert c.blocks[10].header.difficulty == 2 and c.tip.header.difficulty == 3
    assert c.balance(a.address) == 550 and c.validate().accepted


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


@pytest.mark.parametrize('field,value',[('hash','0'*64),('extra',1)])
def test_block_decoder_rejects_invalid_fields(funded,field,value):
    c,*_ = funded
    raw=c.tip.to_dict(); raw[field]=value
    with pytest.raises(ValueError): block_from_dict(raw)


def test_coinbase_network_rejected(funded):
    c,*_=funded
    raw=c.tip.to_dict(); raw['transactions'][0]['network']='OTHER'
    with pytest.raises(ValueError): block_from_dict(raw)


def test_stored_hash_mismatch_rejected(funded,tmp_path):
    c,*_=funded; p=tmp_path/'chain.json'; c.save(p)
    data=json.loads(p.read_text()); data['blocks'][1]['hash']='f'*64
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Stored block hash'): Blockchain.load(p)


def test_stored_body_tampering_detected(funded,tmp_path):
    c,a,b,m=funded; c.add_block(mined(c,m,[tx(a,b)]))
    p=tmp_path/'chain.json'; c.save(p); data=json.loads(p.read_text())
    data['blocks'][3]['transactions'][1]['amount']=999
    p.write_text(json.dumps(data))
    with pytest.raises(ChainValidationError): Blockchain.load(p)


def test_duplicate_json_keys_rejected(tmp_path):
    p=tmp_path/'chain.json'; p.write_text('{"network":"a","network":"b"}')
    with pytest.raises(ValueError,match='Duplicate JSON field'): Blockchain.load(p)


def test_no_untrusted_balance_snapshot(funded,tmp_path):
    c,*_=funded; p=tmp_path/'chain.json'; c.save(p)
    data=json.loads(p.read_text()); data['balances']={'a'*64:999}
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError): Blockchain.load(p)


def test_failed_atomic_save_keeps_old_file(funded,tmp_path,monkeypatch):
    from blockchain import storage
    c,*_=funded; p=tmp_path/'chain.json'; c.save(p); original=p.read_bytes()
    def fail(*args): raise OSError('simulated failure')
    monkeypatch.setattr(storage.os,'replace',fail)
    with pytest.raises(OSError): c.save(p)
    assert p.read_bytes()==original and not list(tmp_path.glob('*.tmp'))


def test_coinbase_wrong_height(funded):
    c,a,b,m = funded
    block = mined(c,m)
    wrong = replace(block.transactions[0],height=99)
    bad = mine_block(Block.create(height=block.header.height,timestamp=block.header.timestamp,
        previous_hash=c.tip.hash,transactions=[wrong],difficulty=block.header.difficulty)).block
    assert c.add_block(bad).code == 'invalid_structure'


def test_second_coinbase_rejected(funded):
    c,a,b,m = funded
    block = mined(c,m)
    second = replace(block.transactions[0],extra_nonce=1)
    bad = mine_block(Block.create(height=block.header.height,timestamp=block.header.timestamp,
        previous_hash=c.tip.hash,transactions=[block.transactions[0],second],difficulty=block.header.difficulty)).block
    assert c.add_block(bad).code == 'invalid_transaction_type'


def test_same_nonce_different_signed_transactions_in_block(funded):
    c,a,b,m = funded
    first, second = tx(a,b,10,0,0), tx(a,b,11,0,0)
    assert c.add_block(mined(c,m,[first,second])).code == 'invalid_nonce'
    assert c.balance(a.address) == 100 and not c.ledger.was_confirmed(first.txid)


def test_missing_or_wrong_network_file(funded,tmp_path):
    c,*_ = funded; p = tmp_path/'chain.json'
    with pytest.raises(FileNotFoundError): Blockchain.load(p)
    c.save(p)
    data=json.loads(p.read_text()); data['network']='OTHER'
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError): Blockchain.load(p)
