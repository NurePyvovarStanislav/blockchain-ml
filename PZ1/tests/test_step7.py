"""Step 7: real HTTP, local encrypted wallets, nonce selection and CLI errors."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from threading import Thread

import pytest

from blockchain import client as client_module
from blockchain.client import ClientError, NodeClient
from blockchain.network import Handler, Node
from blockchain.transaction import Transaction
from blockchain.wallet import Wallet
from cli import main

PASSWORD = "test-only-password-123"


@contextmanager
def live_node():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.node = Node(f"http://127.0.0.1:{server.server_port}")
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield server.node, NodeClient(server.node.address, timeout=3)
    finally:
        server.shutdown()
        server.node.close()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def live():
    with live_node() as result:
        yield result


@pytest.fixture
def wallets():
    return Wallet.generate(), Wallet.generate()


def cli(capsys, *args, password=PASSWORD):
    capsys.readouterr()  # discard server logs from earlier test operations
    code = main(list(map(str, args)), prompt=lambda _: password)
    # The test server shares this process, unlike normal CLI/node processes.
    output = capsys.readouterr().out
    lines = [line for line in output.splitlines()
             if not (line.startswith("http://127.0.0.1:") and
                     (" MINED " in line or " ADOPT " in line))]
    return code, json.loads("\n".join(lines))


def test_signed_payload_contains_no_private_key(monkeypatch, wallets):
    a, b = wallets
    calls = []
    def capture(url, payload=None, timeout=5):
        calls.append(payload)
        return {"accepted": True, "code": "accepted"}
    monkeypatch.setattr(client_module, "request", capture)
    answer = NodeClient().send(a, b.address, 5, 1, nonce=0)
    assert answer["accepted"]
    assert len(calls) == 1
    payload = calls[0]
    assert set(payload) == {"version", "network", "sender", "recipient", "amount",
                            "fee", "nonce", "public_key", "signature"}
    tx = Transaction.from_dict(payload)
    assert tx.verify_signature() and tx.txid == answer["txid"]


def test_timeout_preserves_txid_and_does_not_retry(monkeypatch, wallets):
    a, b = wallets
    calls = []
    def fail(url, payload=None, timeout=5):
        calls.append(payload)
        raise TimeoutError("response timed out")
    monkeypatch.setattr(client_module, "request", fail)
    with pytest.raises(ClientError) as error:
        NodeClient().send(a, b.address, 5, 1, nonce=0)
    assert len(calls) == 1
    result = error.value.result
    assert result["code"] == "transport_error" and result["outcome_unknown"]
    assert result["txid"] == Transaction.from_dict(calls[0]).txid


def test_validate_replays_not_just_header_hashes(live, wallets, monkeypatch):
    _, c = live
    a, b = wallets
    c.mine(a.address)
    c.send(a, b.address, 5, 1)
    c.mine(a.address)
    snapshot = deepcopy(c.chain())
    # Keep the header (and thus header hash) unchanged; alter the signed transfer.
    snapshot["blocks"][2]["transactions"][1]["amount"] = 7
    monkeypatch.setattr(client_module, "request", lambda *a, **kw: snapshot)
    result = c.validate()
    assert not result["accepted"] and result["code"] == "invalid_signature"


def test_sync_and_dynamic_peer_cli(capsys, wallets):
    with live_node() as (a, ca), live_node() as (b, cb):
        ca.mine(wallets[0].address)
        code, result = cli(capsys, "--node", b.address, "peer-add", a.address)
        assert code == 0 and result["peers"] == [a.address]
        assert a.peers == set()
        code, result = cli(capsys, "--node", b.address, "sync")
        assert code == 0 and result["results"][0]["code"] == "adopted_chain"
        assert cb.status()["tip"] == ca.status()["tip"]
        code, result = cli(capsys, "--node", b.address, "sync")
        assert code == 0 and result["results"][0]["code"] == "not_more_work"


def test_demo_startup_accepts_matching_instance_with_different_pid():
    from types import SimpleNamespace
    from demo_step7 import wait_for_node
    url = "http://127.0.0.1:8201"
    client = SimpleNamespace(node=url, status=lambda: {
        "node": url, "pid": 4321, "instance_id": "expected"})
    launcher = SimpleNamespace(pid=1234, poll=lambda: None)
    assert wait_for_node(client, launcher, "expected", "A")["pid"] == 4321
    stale = SimpleNamespace(node=url, status=lambda: {
        "node": url, "pid": 1234, "instance_id": "previous-instance"})
    with pytest.raises(RuntimeError, match="Unexpected node"):
        wait_for_node(stale, launcher, "expected", "A")

def test_create_and_read_encrypted_wallet_cli(tmp_path, capsys):
    path = tmp_path / "wallet.pem"
    code, result = cli(capsys, "wallet-create", path)
    assert code == 0 and result["accepted"]
    assert b"ENCRYPTED PRIVATE KEY" in path.read_bytes()
    address = result["address"]
    assert cli(capsys, "wallet-address", path)[1]["address"] == address
    original = path.read_bytes()
    assert cli(capsys, "wallet-create", path)[0] == 1
    assert path.read_bytes() == original
    assert cli(capsys, "wallet-address", path, password="wrong-password")[0] == 1


def test_cli_mine_send_chain_balance_validate(live, tmp_path, capsys, wallets):
    node, c = live
    a, b = wallets
    path = tmp_path / "alice.pem"
    a.save_encrypted(path, PASSWORD)
    options = ["--node", node.address]
    assert cli(capsys, *options, "mine", a.address)[0] == 0
    for amount, fee, nonce in ((10, 1, 0), (15, 2, 1)):
        code, sent = cli(capsys, *options, "send", path, b.address, amount, "--fee", fee)
        assert code == 0 and sent["nonce"] == nonce
    summary = cli(capsys, *options, "balance", a.address)[1]
    assert summary["balance"] == 50 and summary["reserved"] == 28
    assert summary["available_balance"] == 22 and summary["expected_nonce"] == 2
    code, rejected = cli(capsys, *options, "send", path, b.address, 23)
    assert code == 1 and rejected["code"] == "insufficient_funds"
    assert cli(capsys, *options, "mine", a.address)[0] == 0
    assert cli(capsys, *options, "balance", a.address)[1]["balance"] == 75
    assert cli(capsys, *options, "balance", b.address)[1]["balance"] == 25
    full = cli(capsys, *options, "chain")[1]
    short = cli(capsys, *options, "chain", "--summary")[1]
    assert len(full["blocks"]) == len(short["blocks"]) == 3
    assert cli(capsys, *options, "validate")[1]["code"] == "valid_chain"
