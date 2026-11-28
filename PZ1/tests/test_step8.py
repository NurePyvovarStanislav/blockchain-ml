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


def test_benchmark_summary_and_four_difficulty_graph(tmp_path):
    import csv
    from pathlib import Path
    from collections import Counter
    from benchmark_step8 import summarize, plot, measure

    with pytest.raises(ValueError):
        measure(runs=9)
    source = Path(__file__).resolve().parents[1] / "measurements" / "step8_benchmark_runs.csv"
    with source.open(newline="", encoding="utf-8") as stream:
        rows = [{"difficulty": int(row["difficulty"]), "trial": int(row["trial"]),
                 "attempts": int(row["attempts"]), "seconds": float(row["seconds"])}
                for row in csv.DictReader(stream)]
    counts = Counter(r["difficulty"] for r in rows)
    assert set(counts) == {1, 2, 3, 4} and min(counts.values()) >= 10
    for d, count in counts.items():
        assert {r["trial"] for r in rows if r["difficulty"] == d} == set(range(1, count + 1))
    assert all(r["seconds"] > 0 and r["attempts"] >= 1 for r in rows)
    summary = summarize(rows)
    for d, result in enumerate(summary, 1):
        assert result["difficulty"] == d and result["runs"] == counts[d]
        assert result["min_seconds"] <= result["mean_seconds"] <= result["max_seconds"]
        assert result["theoretical_attempts"] == 16 ** d
    output = tmp_path / "mining.png"
    plot(summary, output)
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
