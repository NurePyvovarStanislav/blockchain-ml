from dataclasses import replace
import pytest
from blockchain.chain import Blockchain, GENESIS_TIMESTAMP
from blockchain.fork import chain_work, choose_chain
from blockchain.network import Node, decode_history, peer_url, decode_json
from blockchain.transaction import create_signed_transaction
from blockchain.wallet import Wallet


@pytest.fixture
def funded():
    alice, bob, miner = [Wallet.generate() for _ in range(3)]
    chain = Blockchain()
    chain.mine_pending(alice.address, timestamp=GENESIS_TIMESTAMP + 10)
    chain.mine_pending(alice.address, timestamp=GENESIS_TIMESTAMP + 20)
    return chain, alice, bob, miner


def mine(chain, wallet, delta=10):
    return chain.mine_pending(wallet.address, timestamp=chain.tip.header.timestamp + delta).block


def fork_with_transfer(funded):
    local, alice, bob, miner = funded
    remote = Blockchain(local.blocks)
    tx = create_signed_transaction(alice, bob.address, 20, 2, 0)
    assert local.submit_transaction(tx).accepted
    mine(local, miner)
    mine(remote, bob)
    mine(remote, bob)
    return local, remote, tx


def test_genesis_has_zero_work():
    assert chain_work(Blockchain().blocks) == 0


def test_work_per_block(funded):
    assert chain_work(funded[0].blocks) == 512


def test_equal_work_keeps_local(funded):
    current, alice, bob, miner = funded
    remote = Blockchain(current.blocks)
    mine(current, miner)
    mine(remote, bob)
    result = choose_chain(current, remote.blocks)
    assert result.chain is current and result.outcome.code == 'not_more_work'


def test_lower_work_keeps_local(funded):
    current, *_ = funded
    assert choose_chain(current, current.blocks[:2]).outcome.code == 'not_more_work'


def test_invalid_more_work_rejected(funded):
    current, alice, bob, miner = funded
    remote = Blockchain(current.blocks)
    block = mine(remote, bob)
    bad = replace(block, header=replace(block.header, previous_hash='f'*64))
    result = choose_chain(current, (*current.blocks, bad))
    assert not result.outcome.accepted and result.chain is current


def test_reorg_rebuilds_state_and_returns_orphan(funded):
    local, remote, tx = fork_with_transfer(funded)
    result = choose_chain(local, remote.blocks)
    assert result.outcome.accepted and result.common_height == 2
    assert result.recovered == (tx,) and result.chain.pending() == (tx,)
    assert result.chain.balance(tx.sender) == 100
    assert result.chain.ledger.next_nonce(tx.sender) == 0
    assert local.balance(tx.sender) == 78  # old object was not mutated
    assert not result.chain.ledger.was_confirmed(tx.txid)
    assert result.chain.validate().accepted


def test_orphan_coinbase_not_returned(funded):
    local, remote, tx = fork_with_transfer(funded)
    coinbase = local.tip.transactions[0]
    result = choose_chain(local, remote.blocks)
    assert result.chain.balance(coinbase.recipient) == 0
    assert not result.chain.ledger.was_confirmed(coinbase.txid)
    assert all(type(x) is type(tx) for x in result.chain.pending())


def test_pending_nonce_after_orphan_recovered(funded):
    local, remote, tx = fork_with_transfer(funded)
    _, alice, bob, _ = funded
    tx2 = create_signed_transaction(alice, bob.address, 5, 1, 1)
    assert local.submit_transaction(tx2).accepted
    result = choose_chain(local, remote.blocks)
    assert result.chain.pending() == (tx, tx2)


def test_conflicting_orphan_is_rejected(funded):
    local, alice, bob, miner = funded
    remote = Blockchain(local.blocks)
    old = create_signed_transaction(alice, bob.address, 20, 1, 0)
    new = create_signed_transaction(alice, miner.address, 30, 1, 0)
    assert local.submit_transaction(old).accepted
    assert remote.submit_transaction(new).accepted
    mine(local, miner)
    mine(remote, bob)
    mine(remote, bob)
    result = choose_chain(local, remote.blocks)
    assert result.outcome.accepted and result.orphaned == (old,)
    assert result.recovered == () and result.dropped == ((old.txid, 'invalid_nonce'),)


def test_confirmed_on_winning_branch_not_requeued(funded):
    local, alice, bob, miner = funded
    remote = Blockchain(local.blocks)
    tx = create_signed_transaction(alice, bob.address, 20, 1, 0)
    for chain in (local, remote):
        assert chain.submit_transaction(tx).accepted
    mine(local, miner)
    mine(remote, bob)
    mine(remote, bob)
    result = choose_chain(local, remote.blocks)
    assert result.outcome.accepted and result.orphaned == () and result.chain.pending() == ()


def test_orphan_with_lost_funding_cannot_return(funded):
    local, alice, bob, miner = funded
    remote = Blockchain(local.blocks)
    mine(local, miner)
    tx = create_signed_transaction(miner, bob.address, 10, 1, 0)
    assert local.submit_transaction(tx).accepted
    mine(local, miner)
    for _ in range(3):
        mine(remote, bob)
    result = choose_chain(local, remote.blocks)
    assert result.outcome.accepted and result.dropped == ((tx.txid, 'insufficient_funds'),)


def test_shorter_chain_with_more_work_wins():
    fast, slow = Blockchain(), Blockchain()
    wallet = Wallet.generate()
    for _ in range(11):
        mine(fast, wallet, 2)
    for _ in range(12):
        mine(slow, wallet, 30)
    assert fast.tip.header.difficulty == 3 and slow.tip.header.difficulty == 1
    assert chain_work(fast.blocks) == 6656 and chain_work(slow.blocks) == 2592
    assert choose_chain(slow, fast.blocks).chain.tip == fast.tip


def test_snapshot_roundtrip(funded):
    node = Node('http://127.0.0.1:8101')
    node.chain = funded[0]
    assert decode_history(node.snapshot()) == node.chain.blocks


def test_wrong_network_rejected():
    with pytest.raises(ValueError):
        decode_history({'network': 'other', 'blocks': []})


def test_claimed_hash_rejected():
    node = Node('http://127.0.0.1:8101')
    data = node.snapshot()
    data['blocks'][0]['hash'] = '0'*64
    with pytest.raises(ValueError):
        decode_history(data)


@pytest.mark.parametrize('url', ['http://example.org:8101', 'https://127.0.0.1:8101',
                                'http://127.0.0.1:0', 'http://127.0.0.1:8101/foo'])
def test_peer_rejects_invalid_addresses(url):
    with pytest.raises(ValueError):
        peer_url(url)


def test_self_peer_rejected():
    with pytest.raises(ValueError):
        Node('http://127.0.0.1:8101', ['http://127.0.0.1:8101'])


def test_duplicate_json_fields_rejected():
    with pytest.raises(ValueError):
        decode_json('{"x":1,"x":2}')


def test_offline_node_rejects_remote_chain(funded):
    node = Node('http://127.0.0.1:8101')
    node.online = False
    assert node.receive_history(funded[0].blocks, remote=True)['code'] == 'offline'
    assert node.status()['height'] == 0


def test_offline_node_rejects_remote_transaction(funded):
    node = Node('http://127.0.0.1:8101')
    node.chain, alice, bob, _ = funded
    node.online = False
    tx = create_signed_transaction(alice, bob.address, 10, 1, 0)
    assert node.submit(tx, remote=True)['code'] == 'offline'
    assert node.submit(tx)['accepted']
    assert node.chain.pending() == (tx,)
