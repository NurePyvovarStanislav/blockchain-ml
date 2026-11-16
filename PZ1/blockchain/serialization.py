"""Deterministic encoding for this deliberately narrow transaction schema.

This is not a general-purpose implementation of canonical JSON.
"""

import json


def canonical_bytes(data: dict) -> bytes:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
