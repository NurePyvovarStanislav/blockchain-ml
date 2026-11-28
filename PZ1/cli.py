"""Step 7: command-line wallet and node management."""
import argparse
import getpass
import json
import math
from pathlib import Path
import warnings

from cryptography.exceptions import UnsupportedAlgorithm

from blockchain.client import ClientError, NodeClient
from blockchain.wallet import Wallet


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return number


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("Expected a nonnegative integer")
    return number


def positive_timeout(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Expected a finite positive timeout")
    return number


def parser():
    p = argparse.ArgumentParser(description="PZ1 wallet and blockchain node CLI")
    p.add_argument("--node", default="http://127.0.0.1:8101",
                   help="Loopback HTTP node URL (before the command)")
    p.add_argument("--timeout", type=positive_timeout, default=120,
                   help="HTTP socket timeout in seconds")
    commands = p.add_subparsers(dest="command", required=True)
    for name, help_text in (("wallet-create", "Create an encrypted local wallet"),
                            ("wallet-address", "Show the address of an encrypted wallet")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("wallet", type=Path, help="Encrypted PEM file")
    send = commands.add_parser("send", help="Sign locally and submit a transfer")
    send.add_argument("wallet", type=Path)
    send.add_argument("recipient")
    send.add_argument("amount", type=positive_int)
    send.add_argument("--fee", type=nonnegative_int, default=0)
    send.add_argument("--nonce", type=nonnegative_int,
                      help="Optional manual nonce; default is the node's expected nonce")
    mine = commands.add_parser("mine", help="Mine a block to an address")
    mine.add_argument("address")
    chain = commands.add_parser("chain", help="Show the entire accepted history")
    chain.add_argument("--summary", action="store_true", help="Only headers' key fields")
    balance = commands.add_parser("balance", help="Show confirmed and spendable balance")
    balance.add_argument("address")
    commands.add_parser("validate", help="Download and independently validate history")
    peer = commands.add_parser("peer-add", help="Add an outgoing neighbor")
    peer.add_argument("peer")
    commands.add_parser("sync", help="Synchronize with known neighbors")
    commands.add_parser("status", help="Show node height, tip, peers and mempool")
    return p


def ask_password(message):
    # Do not silently fall back to an echoed password on a noninteractive input.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(message)


def run(args, prompt):
    if args.command == "wallet-create":
        password = prompt("Wallet password (at least 12 characters): ")
        if password != prompt("Confirm password: "):
            raise ValueError("Passwords do not match")
        wallet = Wallet.generate()
        wallet.save_encrypted(args.wallet, password)
        return {"accepted": True, "wallet": str(args.wallet), "address": wallet.address,
                "public_key": wallet.public_key_hex}
    if args.command == "wallet-address":
        wallet = Wallet.load_encrypted(args.wallet, prompt("Wallet password: "))
        return {"address": wallet.address, "public_key": wallet.public_key_hex}

    client = NodeClient(args.node, args.timeout)
    if args.command == "send":
        wallet = Wallet.load_encrypted(args.wallet, prompt("Wallet password: "))
        return client.send(wallet, args.recipient, args.amount, args.fee, args.nonce)
    if args.command == "mine":
        return client.mine(args.address)
    if args.command == "chain":
        return client.chain(args.summary)
    if args.command == "balance":
        return client.account(args.address)
    if args.command == "validate":
        return client.validate()
    if args.command == "peer-add":
        return client.add_peer(args.peer)
    if args.command == "sync":
        return client.sync()
    return client.status()


def main(argv=None, *, prompt=None):
    args = parser().parse_args(argv)
    try:
        result = run(args, prompt or ask_password)
    except ClientError as exc:
        result = exc.result
    except getpass.GetPassWarning:
        result = {"accepted": False, "code": "interactive_password_required",
                  "reason": "Use an interactive terminal for wallet passwords"}
    except (OSError, ValueError, TypeError, UnsupportedAlgorithm, EOFError) as exc:
        result = {"accepted": False, "code": "client_error", "reason": str(exc)}
    except KeyboardInterrupt:
        result = {"accepted": False, "code": "cancelled", "reason": "Interrupted by user"}
    print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))
    return 1 if result.get("accepted") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
