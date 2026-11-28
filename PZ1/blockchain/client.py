"""Step 7: local HTTP client. Private keys never enter an HTTP payload."""
from dataclasses import asdict
from http.client import HTTPException
import re
from urllib.error import HTTPError

from .chain import validate_chain
from .network import MAX_BYTES, decode_history, decode_json, peer_url, request
from .transaction import create_signed_transaction
from .wallet import Wallet


def checked_address(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Address must contain 64 lowercase hexadecimal characters")
    return value


class ClientError(Exception):
    """Structured error for a failed client operation, suitable for JSON output."""
    def __init__(self, code: str, reason: str, **details):
        self.result = {"accepted": False, "code": code, "reason": reason, **details}
        super().__init__(reason)


class NodeClient:
    def __init__(self, node="http://127.0.0.1:8101", timeout=120):
        self.node = peer_url(node)
        self.timeout = timeout

    def _call(self, path, payload=None):
        try:
            result = request(self.node + path, payload, timeout=self.timeout)
        except HTTPError as exc:
            # Read only a bounded response; never follow redirects.
            try:
                raw = exc.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Response too large")
                error = decode_json(raw)
                if not isinstance(error, dict):
                    raise ValueError("Expected JSON error object")
                code = str(error.get("code", "http_error"))
                reason = str(error.get("reason", exc.reason))
            except (OSError, HTTPException, ValueError, TypeError, RecursionError):
                code, reason = "http_error", str(exc.reason)
            finally:
                exc.close()
            raise ClientError(code, reason, http_status=exc.code,
                              outcome_unknown=payload is not None and (exc.code >= 500 or code == "response_too_large")) from exc
        except (OSError, HTTPException, ValueError, TypeError, RecursionError) as exc:
            # A failed response does not prove that a POST was not committed.
            raise ClientError("transport_error", str(exc),
                              outcome_unknown=payload is not None) from exc
        if type(result) is not dict:
            raise ClientError("invalid_response", "Expected JSON object",
                              outcome_unknown=payload is not None)
        return result

    def _mutation(self, path, payload):
        result = self._call(path, payload)
        if type(result.get("accepted")) is not bool:
            raise ClientError("invalid_response", "Missing operation outcome",
                              outcome_unknown=True)
        return result

    def status(self):
        return self._call("/status")

    def account(self, address: str):
        address = checked_address(address)
        result = self._call("/balance/" + address)
        keys = ("balance", "next_nonce", "reserved", "available_balance", "expected_nonce")
        if (result.get("address") != address
                or any(type(result.get(k)) is not int or result[k] < 0 for k in keys)
                or result["balance"] - result["reserved"] != result["available_balance"]
                or result["expected_nonce"] < result["next_nonce"]):
            raise ClientError("invalid_response", "Malformed account summary")
        return result

    def send(self, wallet: Wallet, recipient: str, amount: int, fee: int, nonce=None):
        recipient = checked_address(recipient)
        if nonce is None:
            nonce = self.account(wallet.address)["expected_nonce"]
        # Only the signed transaction, including the public key, goes to the node.
        tx = create_signed_transaction(wallet, recipient, amount, fee, nonce)
        try:
            result = self._mutation("/transactions", tx.to_dict())
        except ClientError as exc:
            # Useful for checking a possibly committed transaction after timeout.
            exc.result.update(txid=tx.txid, nonce=tx.nonce)
            raise
        return {**result, "txid": tx.txid, "nonce": tx.nonce}

    def mine(self, address: str):
        return self._mutation("/mine", {"miner": checked_address(address)})

    def chain(self, summary=False):
        data = self._call("/chain")
        blocks = self._decode(data)
        if summary:
            return {"network": data["network"], "blocks": [
                {"height": b.header.height, "hash": b.hash,
                 "difficulty": b.header.difficulty, "transactions": len(b.transactions)}
                for b in blocks]}
        return data

    @staticmethod
    def _decode(data):
        try:
            return decode_history(data)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise ClientError("invalid_history", str(exc)) from exc

    def validate(self):
        # Not a boolean supplied by the node: replay the downloaded history here.
        blocks = self._decode(self._call("/chain"))
        result = validate_chain(blocks)
        answer = asdict(result)
        if blocks:
            answer.update(height=blocks[-1].header.height, tip=blocks[-1].hash)
        return answer

    def add_peer(self, peer):
        return self._mutation("/peers", {"peer": peer_url(peer)})

    def sync(self):
        answer = self._call("/sync", {})
        if type(answer.get("results")) is not list or any(
                type(item) is not dict or type(item.get("accepted")) is not bool
                for item in answer["results"]):
            raise ClientError("invalid_response", "Missing synchronization results",
                              outcome_unknown=True)
        status = answer.get("status")
        if type(status) is not dict or type(status.get("online")) is not bool:
            raise ClientError("invalid_response", "Missing synchronization status",
                              outcome_unknown=True)
        if not status["online"]:
            return {**answer, "accepted": False, "code": "offline"}
        if not answer["results"]:
            return {**answer, "accepted": False, "code": "no_peers"}
        # Equal/lower work is normal, transport/validation errors are not.
        answer["accepted"] = all(item["accepted"] or item.get("code") == "not_more_work"
                                 for item in answer["results"])
        return answer
