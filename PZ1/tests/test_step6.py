from dataclasses import replace
import pytest
from blockchain.chain import Blockchain, GENESIS_TIMESTAMP
from blockchain.fork import chain_work, choose_chain
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


def test_invalid_more_work_rejected(funded):
    current, alice, bob, miner = funded
    remote = Blockchain(current.blocks)
    block = mine(remote, bob)
    bad = replace(block, header=replace(block.header, previous_hash='f'*64))
    result = choose_chain(current, (*current.blocks, bad))
    assert not result.outcome.accepted and result.chain is current


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

def test_equal_or_lower_work_keeps_local(funded):
    current, alice, bob, miner = funded
    remote = Blockchain(current.blocks)
    mine(current, miner)
    mine(remote, bob)
    assert chain_work(current.blocks) == 768
    for history in (remote.blocks, remote.blocks[:2]):
        result = choose_chain(current, history)
        assert result.chain is current and result.outcome.code == "not_more_work"


def test_reorg_recovers_orphan_and_pending_nonce_not_coinbase(funded):
    local, remote, tx = fork_with_transfer(funded)
    _, alice, bob, _ = funded
    tx2 = create_signed_transaction(alice, bob.address, 5, 1, 1)
    assert local.submit_transaction(tx2).accepted
    coinbase = local.tip.transactions[0]
    result = choose_chain(local, remote.blocks)
    assert result.outcome.accepted and result.common_height == 2
    assert result.recovered == (tx,) and result.chain.pending() == (tx, tx2)
    assert result.chain.balance(tx.sender) == 100
    assert result.chain.ledger.next_nonce(tx.sender) == 0
    assert local.balance(tx.sender) == 78
    assert not result.chain.ledger.was_confirmed(tx.txid)
    assert not result.chain.ledger.was_confirmed(coinbase.txid)
    assert result.chain.balance(coinbase.recipient) == 0
    assert result.chain.validate().accepted


def test_three_process_network_propagation_and_fork():
    import socket
    import subprocess
    import sys
    from pathlib import Path
    # Find three free consecutive ports; the existing demo checks actual
    # independent processes, propagation, partition, recovery and final balances.
    import random
    base = None
    for candidate in random.sample(range(18000, 58000, 3), 50):
        probes = []
        try:
            for port in range(candidate, candidate + 3):
                sock = socket.socket()
                probes.append(sock)
                sock.bind(("127.0.0.1", port))
            base = candidate
            break
        except OSError:
            pass
        finally:
            for sock in probes:
                sock.close()
    assert base is not None, "Could not reserve three consecutive loopback ports"
    script = Path(__file__).resolve().parents[1] / "demo_step6.py"
    process = subprocess.run([sys.executable, str(script), "--base-port", str(base)],
                             capture_output=True, text=True, timeout=75,
                             cwd=script.parent)
    assert process.returncode == 0, process.stdout + process.stderr
    assert "PASS: three independent processes converged." in process.stdout
