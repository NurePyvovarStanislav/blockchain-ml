"""Step 5: strict JSON decoding and atomic local history persistence."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from collections.abc import Sequence

from .block import Block, BlockHeader
from .coinbase import CoinbaseTransaction
from .config import NETWORK_ID
from .transaction import Transaction

FORMAT_VERSION = 1


def _object(data, fields: set[str], name: str) -> None:
    if type(data) is not dict or set(data) != fields:
        raise ValueError(f"Invalid {name} fields")


def block_from_dict(data: dict) -> Block:
    """Decode an untrusted block and compare its claimed hash with computed hash.

    This is NOT full consensus validation; callers must replay or append it.
    """
    _object(data, {"header", "transactions", "hash"}, "block")
    _object(data["header"], {"height", "timestamp", "previous_hash", "merkle_root",
                            "difficulty", "nonce"}, "header")
    header = BlockHeader(**data["header"])
    if type(data["transactions"]) is not list:
        raise ValueError("Transactions must be a JSON array")
    txs = []
    for raw in data["transactions"]:
        if type(raw) is not dict:
            raise ValueError("Transaction must be a JSON object")
        if raw.get("type") == "coinbase":
            _object(raw, {"type", "version", "network", "height", "recipient",
                          "amount", "extra_nonce"}, "coinbase")
            if type(raw["version"]) is not int or raw["version"] != 1 or raw["network"] != NETWORK_ID:
                raise ValueError("Unsupported coinbase version/network")
            txs.append(CoinbaseTransaction(height=raw["height"], recipient=raw["recipient"],
                        amount=raw["amount"], extra_nonce=raw["extra_nonce"]))
        else:
            txs.append(Transaction.from_dict(raw))
    block = Block(header, tuple(txs))
    if type(data["hash"]) is not str or data["hash"] != block.hash:
        raise ValueError("Stored block hash does not match SHA-256 of its header")
    return block


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def save_blocks(blocks: Sequence[Block], path: str | Path) -> None:
    """Write a temporary file on the same filesystem, then replace destination."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"format_version": FORMAT_VERSION, "network": NETWORK_ID,
               "blocks": [block.to_dict() for block in blocks]}
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                prefix=target.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_blocks(path: str | Path) -> tuple[Block, ...]:
    with Path(path).open(encoding="utf-8") as stream:
        data = json.load(stream, object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)
    _object(data, {"format_version", "network", "blocks"}, "chain file")
    if type(data["format_version"]) is not int or data["format_version"] != FORMAT_VERSION:
        raise ValueError("Unsupported chain file format")
    if data["network"] != NETWORK_ID or type(data["blocks"]) is not list:
        raise ValueError("Wrong network or invalid block list")
    return tuple(block_from_dict(item) for item in data["blocks"])
