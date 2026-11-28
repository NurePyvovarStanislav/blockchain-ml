"""Live Step 7 demonstration: three nodes, real CLI commands and encrypted wallets."""
import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from blockchain.client import NodeClient, ClientError
from cli import main as cli_main


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_for(condition, description, timeout=20):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            if condition():
                return
        except ClientError:
            pass
        sleep(0.05)
    raise RuntimeError("Timeout: " + description)


def wait_for_node(client, child, instance_id, label, timeout=20):
    """Identify our new server even when a venv launcher has a different PID."""
    deadline = monotonic() + timeout
    last_error = "no response"
    while monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"Node {label} exited during startup (code {child.returncode}); "
                               "check its output and try another --base-port")
        try:
            status = client.status()
        except ClientError as exc:
            last_error = str(exc)
            sleep(0.05)
            continue
        if status.get("node") != client.node or status.get("instance_id") != instance_id:
            raise RuntimeError(f"Unexpected node at {client.node}; port occupied or stale node files. "
                               "Replace the complete hotfix or choose another --base-port.")
        return status
    raise RuntimeError(f"Timeout: node {label} startup ({last_error})")


def main(base_port=8201, pause=False):
    nodes = [f"http://127.0.0.1:{base_port + i}" for i in range(3)]
    clients = [NodeClient(url, timeout=3) for url in nodes]
    children = []
    instance_ids = [secrets.token_hex(16) for _ in nodes]
    password = secrets.token_urlsafe(24)  # demo only; never printed or sent over HTTP

    def run(i, *args, ok=True, show=None):
        buffer = StringIO()
        print(f"{'ABC'[i]}> python cli.py --node {nodes[i]} " + " ".join(str(x) for x in args), flush=True)
        with redirect_stdout(buffer):
            exit_code = cli_main(["--node", nodes[i], *map(str, args)], prompt=lambda _: password)
        result = json.loads(buffer.getvalue())
        require(exit_code == (0 if ok else 1), f"Unexpected CLI result: {result}")
        # Only shorten the demonstration output; the real CLI prints full JSON.
        print(json.dumps(show(result) if show else result, indent=2), flush=True)
        return result

    def phase(text):
        print("\n" + text, flush=True)
        if pause:
            input("Press Enter to continue...")

    def brief_mine(r):
        return {"accepted": r["accepted"], "height": r["block"]["header"]["height"],
                "hash": r["block"]["hash"], "relay_queued": r["relay_queued"]}

    def brief_sync(r):
        return {"accepted": r["accepted"], "results": [
            {"peer": x["peer"], "code": x["code"]} for x in r["results"]],
            "height": r["status"]["height"], "tip": r["status"]["tip"]}

    with TemporaryDirectory(prefix="pz1-step7-") as temp:
        alice_path, bob_path = Path(temp) / "alice.pem", Path(temp) / "bob.pem"
        try:
            for i in range(3):
                children.append(subprocess.Popen(
                    [sys.executable, "-u", str(Path(__file__).with_name("node_step6.py")),
                     "--port", str(base_port + i), "--demo-instance", instance_ids[i]],
                    stdin=subprocess.PIPE))
            for i, client in enumerate(clients):
                wait_for_node(client, children[i], instance_ids[i], "ABC"[i])
            phase("1. CREATE ENCRYPTED LOCAL WALLETS AND CONNECT A <-> B")
            alice = run(0, "wallet-create", alice_path, show=lambda r: {
                "wallet": "alice.pem", "address": r["address"]})["address"]
            bob = run(0, "wallet-create", bob_path, show=lambda r: {
                "wallet": "bob.pem", "address": r["address"]})["address"]
            require(b"ENCRYPTED PRIVATE KEY" in alice_path.read_bytes(), "Wallet must be encrypted")
            run(0, "peer-add", nodes[1])
            run(1, "peer-add", nodes[0])
            phase("2. FUND ALICE WITH MINING")
            run(0, "mine", alice, show=brief_mine)
            wait_for(lambda: clients[1].status()["height"] == 1, "B receives block 1")
            require(run(0, "balance", alice)["balance"] == 50, "Initial mining reward")
            phase("3. SEND TWO TRANSFERS BEFORE CONFIRMATION")
            first = run(0, "send", alice_path, bob, "10", "--fee", "1")
            second = run(0, "send", alice_path, bob, "15", "--fee", "2")
            require((first["nonce"], second["nonce"]) == (0, 1), "Expected mempool nonces 0 and 1")
            summary = run(0, "balance", alice)
            require((summary["balance"], summary["reserved"], summary["available_balance"],
                     summary["next_nonce"], summary["expected_nonce"]) == (50, 28, 22, 0, 2),
                    "Incorrect mempool-aware balance")
            require(run(0, "balance", bob)["available_balance"] == 0, "Unconfirmed incoming is not spendable")
            bad = run(0, "send", alice_path, bob, "23", "--fee", "0", ok=False)
            require(bad["code"] == "insufficient_funds", "Expected mempool rejection")
            wait_for(lambda: len(clients[1].status()["pending"]) == 2, "B receives both transfers")
            phase("4. CONFIRM BOTH TRANSFERS AND INSPECT THE CHAIN")
            run(0, "mine", alice, show=brief_mine)
            wait_for(lambda: clients[1].status()["height"] == 2, "B receives block 2")
            require(run(0, "balance", alice)["balance"] == 75, "Expected 75 on Alice")
            require(run(1, "balance", bob)["balance"] == 25, "Expected 25 on Bob")
            run(0, "chain", "--summary")
            require(run(0, "validate")["accepted"], "Client validation failed")
            phase("5. NODE C JOINS LATE AND SYNCHRONIZES")
            require(run(2, "status", show=lambda r: {"height": r["height"], "peers": r["peers"]})["height"] == 0,
                    "C should start at genesis")
            run(2, "peer-add", nodes[0])
            run(0, "peer-add", nodes[2])
            result = run(2, "sync", show=brief_sync)
            require(result["status"]["height"] == 2, "C should adopt block 2")
            run(2, "validate")
            balances = [(c.account(alice)["balance"], c.account(bob)["balance"]) for c in clients]
            require(balances == [(75, 25)] * 3, "Balances differ")
            statuses = [c.status() for c in clients]
            require(len({s["tip"] for s in statuses}) == 1, "Tips differ")
            require(all(s["height"] == 2 and s["work"] == 512 and not s["pending"]
                        for s in statuses), "Unexpected final node state")
            print("FINAL node states:", json.dumps([
                {k: s[k] for k in ("height", "work", "tip", "pending")} for s in statuses
            ]), flush=True)
            print("\nFINAL Alice/Bob balances A, B, C:", balances, flush=True)
            print("PASS: all eight required Step 7 commands demonstrated.", flush=True)
        finally:
            # Close every parent pipe first, so the actual server interpreter
            # exits even if Popen represents a Windows venv launcher.
            for child in children:
                if child.stdin is not None:
                    child.stdin.close()
            for child in children:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-port", type=int, default=8201)
    parser.add_argument("--pause", action="store_true")
    args = parser.parse_args()
    main(args.base_port, args.pause)
