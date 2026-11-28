"""Minimal regression checks for the final step; the live demos cover details."""
import pytest
from dataclasses import replace

from blockchain.chain import Blockchain
from blockchain.light_client import LightClient, make_proof, verify_proof
from blockchain.merkle import merkle_root
from demo_step8 import attack_scenarios, merkle_demo, majority_demo
from simulate_majority_step8 import simulate


def test_five_required_attacks():
    assert attack_scenarios() == {1: "invalid_signature", 2: "invalid_link",
                                 3: "invalid_signature", 4: "insufficient_funds",
                                 5: "invalid_nonce"}


def test_merkle_membership_and_light_client():
    for count in (1, 2, 3, 4, 5):
        ids = [f"{i+1:064x}" for i in range(count)]
        root = merkle_root(ids)
        for i, txid in enumerate(ids):
            proof = make_proof(ids, i)
            assert verify_proof(txid, proof, root)
            assert not verify_proof("0"*64, proof, root)
            assert not verify_proof(txid, proof, "0"*64)
    chain = Blockchain()
    chain.mine_pending("a"*64, timestamp=chain.tip.header.timestamp+1)
    headers = tuple(b.header for b in chain.blocks)
    with pytest.raises(ValueError):
        LightClient((headers[0], replace(headers[1], previous_hash="0"*64)))
    assert merkle_demo()


def test_controlled_majority_fork_and_model():
    assert majority_demo() == "adopted_chain"
    assert simulate(0, trials=10, horizon=10)["probability"] == 0
    assert simulate(1, trials=10, horizon=10)["probability"] == 1
    assert simulate(1, trials=10, horizon=3)["probability"] == 0  # tie not success
