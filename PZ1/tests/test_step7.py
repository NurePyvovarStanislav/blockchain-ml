"""Step 7: real HTTP, local encrypted wallets, nonce selection and CLI errors."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from threading import Thread
import warnings

import pytest

from blockchain import client as client_module
from blockchain.client import ClientError, NodeClient
from blockchain.network import Handler, Node
from blockchain.transaction import Transaction
from blockchain.wallet import Wallet
from cli import main, ask_password

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


def test_create_encrypted_wallet_offline(tmp_path, capsys):
    path = tmp_path / "wallet.pem"
    code, result = cli(capsys, "wallet-create", path)
    assert code == 0 and result["accepted"]
    assert set(result) == {"accepted", "wallet", "address", "public_key"}
    assert b"ENCRYPTED PRIVATE KEY" in path.read_bytes()
    assert PASSWORD.encode() not in path.read_bytes()
    assert Wallet.load_encrypted(path, PASSWORD).address == result["address"]


def test_wallet_address_loads_correct_key(tmp_path, capsys):
    path = tmp_path / "alice.pem"
    wallet = Wallet.generate()
    wallet.save_encrypted(path, PASSWORD)
    code, result = cli(capsys, "wallet-address", path)
    assert code == 0 and result["address"] == wallet.address


def test_wallet_never_overwrites_existing_file(tmp_path, capsys):
    path = tmp_path / "existing.pem"
    path.write_bytes(b"keep this content")
    code, result = cli(capsys, "wallet-create", path)
    assert code == 1 and result["code"] == "client_error"
    assert path.read_bytes() == b"keep this content"


@pytest.mark.parametrize("password", ["short", ""])
def test_short_password_rejected(tmp_path, capsys, password):
    p = tmp_path / "alice.pem"
    code, result = cli(capsys, "wallet-create", p, password=password)
    assert code == 1 and result["accepted"] is False and not p.exists()


def test_password_confirmation_mismatch(tmp_path, capsys):
    path = tmp_path / "wallet.pem"
    answers = iter([PASSWORD, "different-password"])
    code = main(["wallet-create", str(path)], prompt=lambda _: next(answers))
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and "match" in result["reason"]
    assert not path.exists()


def test_wrong_password_cannot_open_wallet(tmp_path, capsys):
    path = tmp_path / "wallet.pem"
    Wallet.generate().save_encrypted(path, PASSWORD)
    code, result = cli(capsys, "wallet-address", path, password="wrong-password")
    assert code == 1 and result["accepted"] is False


def test_password_prompt_disallows_echo_fallback(monkeypatch):
    import getpass
    def echo_fallback(_):
        warnings.warn("not hidden", getpass.GetPassWarning)
        return "not-used"
    monkeypatch.setattr(getpass, "getpass", echo_fallback)
    with pytest.raises(getpass.GetPassWarning):
        ask_password("Password: ")


def test_account_pending_nonce_and_reserved_balance(live, wallets):
    node, c = live
    a, b = wallets
    assert c.mine(a.address)["accepted"]
    assert c.send(a, b.address, 10, 1)["nonce"] == 0
    assert c.send(a, b.address, 15, 2)["nonce"] == 1
    summary = c.account(a.address)
    assert summary == {"address": a.address, "balance": 50,
                       "next_nonce": 0, "reserved": 28,
                       "available_balance": 22, "expected_nonce": 2}
    assert c.account(b.address)["available_balance"] == 0
    assert c.send(a, b.address, 23, 0)["code"] == "insufficient_funds"
    assert c.account(a.address) == summary


def test_after_confirmation_nonce_and_balance(live, wallets):
    _, c = live
    a, b = wallets
    assert c.mine(a.address)["accepted"]
    assert c.send(a, b.address, 10, 1)["accepted"]
    assert c.send(a, b.address, 15, 2)["accepted"]
    assert c.mine(a.address)["accepted"]
    assert c.account(a.address) == {"address": a.address, "balance": 75,
                                   "next_nonce": 2, "reserved": 0,
                                   "available_balance": 75, "expected_nonce": 2}
    assert c.account(b.address)["balance"] == 25


def test_manual_stale_nonce_rejected_by_node(live, wallets):
    _, c = live
    a, b = wallets
    c.mine(a.address)
    assert c.send(a, b.address, 5, 1, nonce=0)["accepted"]
    result = c.send(a, b.address, 6, 1, nonce=0)
    assert not result["accepted"] and result["code"] == "invalid_nonce"


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


def test_malformed_mutation_response_is_not_success(monkeypatch, wallets):
    monkeypatch.setattr(client_module, "request", lambda *a, **kw: {})
    with pytest.raises(ClientError) as error:
        NodeClient().mine(wallets[0].address)
    assert error.value.result["code"] == "invalid_response"
    assert error.value.result["outcome_unknown"]


def test_malformed_account_response_rejected(monkeypatch, wallets):
    a, _ = wallets
    monkeypatch.setattr(client_module, "request", lambda *args, **kw: {
        "address": a.address, "balance": 10, "next_nonce": 0,
        "reserved": 2, "available_balance": 10, "expected_nonce": 1,
    })
    with pytest.raises(ClientError) as error:
        NodeClient().account(a.address)
    assert error.value.result["code"] == "invalid_response"


def test_cli_mine_send_chain_balance_validate(live, tmp_path, capsys, wallets):
    node, c = live
    a, b = wallets
    path = tmp_path / "alice.pem"
    a.save_encrypted(path, PASSWORD)
    options = ["--node", node.address]
    assert cli(capsys, *options, "mine", a.address)[0] == 0
    code, sent = cli(capsys, *options, "send", path, b.address, 10, "--fee", 1)
    assert code == 0 and sent["nonce"] == 0
    assert cli(capsys, *options, "balance", a.address)[1]["available_balance"] == 39
    full = cli(capsys, *options, "chain")[1]
    short = cli(capsys, *options, "chain", "--summary")[1]
    assert len(full["blocks"]) == len(short["blocks"]) == 2
    assert set(short["blocks"][1]) == {"height", "hash", "difficulty", "transactions"}
    assert cli(capsys, *options, "validate")[1]["code"] == "valid_chain"


def test_cli_rejected_transaction_exit_code(live, tmp_path, capsys, wallets):
    node, _ = live
    a, b = wallets
    path = tmp_path / "alice.pem"
    a.save_encrypted(path, PASSWORD)
    code, result = cli(capsys, "--node", node.address, "send", path, b.address, 1)
    assert code == 1 and result["code"] == "insufficient_funds"


def test_validate_rejects_claimed_hash(live, wallets, monkeypatch):
    _, c = live
    c.mine(wallets[0].address)
    snapshot = deepcopy(c.chain())
    snapshot["blocks"][1]["hash"] = "0" * 64
    monkeypatch.setattr(client_module, "request", lambda *a, **kw: snapshot)
    with pytest.raises(ClientError) as error:
        c.validate()
    assert error.value.result["code"] == "invalid_history"


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


def test_no_peer_and_offline_sync_are_explicit(live):
    node, c = live
    assert c.sync()["code"] == "no_peers"
    node.set_online(False)
    assert c.sync()["code"] == "offline"


def test_peer_add_self_http_error(live, capsys):
    node, _ = live
    code, result = cli(capsys, "--node", node.address, "peer-add", node.address)
    assert code == 1 and result["http_status"] == 400
    assert result["code"] == "bad_request"


@pytest.mark.parametrize("url", ["http://example.org:8101", "https://127.0.0.1:8101"])
def test_non_loopback_node_rejected(capsys, url):
    code, result = cli(capsys, "--node", url, "status")
    assert code == 1 and result["code"] == "client_error"


@pytest.mark.parametrize("args", [
    ["--timeout", "nan", "status"], ["send", "x", "y", "0"],
    ["send", "x", "y", "1", "--fee", "-1"], ["send", "x", "y", "1", "--nonce", "-1"],
])
def test_bad_cli_arguments(args):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_demo_startup_accepts_matching_instance_with_different_pid():
    from types import SimpleNamespace
    from demo_step7 import wait_for_node
    url = "http://127.0.0.1:8201"
    client = SimpleNamespace(node=url, status=lambda: {
        "node": url, "pid": 4321, "instance_id": "expected"})
    launcher = SimpleNamespace(pid=1234, poll=lambda: None)
    assert wait_for_node(client, launcher, "expected", "A")["pid"] == 4321


@pytest.mark.parametrize("instance_id", [None, "stale"])
def test_demo_startup_refuses_previous_node_on_same_port(instance_id):
    from types import SimpleNamespace
    from demo_step7 import wait_for_node
    url = "http://127.0.0.1:8201"
    client = SimpleNamespace(node=url, status=lambda: {
        "node": url, "pid": 1234, "instance_id": instance_id})
    launcher = SimpleNamespace(pid=1234, poll=lambda: None)
    with pytest.raises(RuntimeError, match="Unexpected node"):
        wait_for_node(client, launcher, "expected", "A")


def test_demo_startup_reports_child_exit_immediately():
    from types import SimpleNamespace
    from demo_step7 import wait_for_node
    launcher = SimpleNamespace(poll=lambda: 1, returncode=1)
    with pytest.raises(RuntimeError, match="exited during startup"):
        wait_for_node(None, launcher, "expected", "A")


def test_demo_child_reports_instance_and_exits_on_parent_pipe_close():
    from pathlib import Path
    import socket
    import subprocess
    import sys
    from demo_step7 import wait_for_node

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    script = Path(__file__).resolve().parents[1] / "node_step6.py"
    child = subprocess.Popen(
        [sys.executable, "-u", str(script), "--port", str(port),
         "--demo-instance", "test-instance"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        status = wait_for_node(NodeClient(f"http://127.0.0.1:{port}", timeout=1),
                               child, "test-instance", "A")
        assert status["instance_id"] == "test-instance"
        child.stdin.close()
        assert child.wait(timeout=5) == 0
    finally:
        if child.stdin is not None and not child.stdin.closed:
            child.stdin.close()
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stderr.close()
