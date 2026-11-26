"""Local educational HTTP network: optimistic mining and bounded async relay."""
import json
import logging
import os
import re
from collections import deque
from dataclasses import asdict
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from time import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from .chain import Blockchain, ChainValidationError, MAX_FUTURE_SECONDS, POLICY
from .config import NETWORK_ID
from .fork import chain_work, choose_chain
from .mining import create_mining_candidate, mine_block
from .storage import block_from_dict, _unique_keys, _invalid_constant
from .transaction import Transaction

MAX_BYTES = 4 * 1024 * 1024
MAX_PEERS = 16
MAX_RELAY_JOBS = 64
# HTTPException is not an OSError; URLError is an OSError subclass.
PEER_ERRORS = (OSError, HTTPException, ValueError, TypeError, KeyError,
               OverflowError, RecursionError)
LOG = logging.getLogger(__name__)


def peer_url(value):
    if not isinstance(value, str) or not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', value):
        raise ValueError('Peer must be http://127.0.0.1:PORT')
    if not 1 <= int(value.rsplit(':', 1)[1]) <= 65535:
        raise ValueError('Invalid peer port')
    return 'http://127.0.0.1:' + str(int(value.rsplit(':', 1)[1]))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def decode_json(data):
    return json.loads(data, object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)


def request(url, payload=None, timeout=5):
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode('utf-8')
    if data is not None and len(data) > MAX_BYTES:
        raise ValueError('HTTP request too large')
    req = Request(url, data=data, headers={'Content-Type': 'application/json'})
    # Loopback HTTP only: bypass proxies and redirects.
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=timeout) as response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('HTTP response too large')
        return decode_json(raw)


def decode_history(data):
    if type(data) is not dict or set(data) != {'network', 'blocks'} or data['network'] != NETWORK_ID:
        raise ValueError('Wrong network or chain envelope')
    if type(data['blocks']) is not list:
        raise ValueError('Expected block list')
    return tuple(block_from_dict(b) for b in data['blocks'])


class Node:
    def __init__(self, address, peers=()):
        self.address = peer_url(address)
        self.lock = RLock()
        self.chain = Blockchain()
        self.peers = set()
        self.online = True
        self._network_epoch = 0
        self._relay_queue = Queue(maxsize=MAX_RELAY_JOBS)
        self._relay_worker = None
        self._relay_stop = Event()
        self._relay_errors = deque(maxlen=20)
        for peer in peers:
            self.add_peer(peer)

    def add_peer(self, peer):
        peer = peer_url(peer)
        with self.lock:
            if peer == self.address:
                raise ValueError('Cannot add self as peer')
            if len(self.peers) >= MAX_PEERS and peer not in self.peers:
                raise ValueError('Too many peers')
            self.peers.add(peer)

    def set_online(self, online):
        if type(online) is not bool:
            raise ValueError('online must be boolean')
        with self.lock:
            if self.online != online:
                self.online = online
                # Queued messages from before a partition must not be sent
                # after the node reconnects. Already in-flight HTTP may finish.
                self._network_epoch += 1
            return self.status()

    def snapshot(self):
        with self.lock:
            return {'network': NETWORK_ID, 'blocks': [b.to_dict() for b in self.chain.blocks]}

    def status(self):
        with self.lock:
            return {'node': self.address, 'pid': os.getpid(), 'online': self.online, 'peers': sorted(self.peers),
                    'height': self.chain.tip.header.height, 'tip': self.chain.tip.hash,
                    'work': chain_work(self.chain.blocks),
                    'pending': [tx.txid for tx in self.chain.pending()],
                    'relay_queue_size': self._relay_queue.qsize(),
                    'relay_errors': list(self._relay_errors)}

    def _record_relay_error(self, peer, path, exc):
        with self.lock:
            self._relay_errors.append({'peer': peer, 'path': path, 'error': str(exc)})

    def _relay_loop(self):
        """One worker per node; no state lock during outbound HTTP."""
        while not self._relay_stop.is_set():
            try:
                epoch, peers, messages = self._relay_queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                # FIFO jobs, and for a reorg send the history before recovered
                # transfers. Another node may still receive messages out of order.
                for path, payload in messages:
                    for peer in peers:
                        with self.lock:
                            if (not self.online or epoch != self._network_epoch
                                    or self._relay_stop.is_set()):
                                break
                        try:
                            result = request(peer + path, payload)
                            if type(result) is not dict:
                                raise ValueError('Expected JSON response object')
                            if result.get('code') == 'offline':
                                raise ValueError('Peer is offline')
                        except PEER_ERRORS as exc:
                            self._record_relay_error(peer, path, exc)
                        except Exception as exc:
                            LOG.exception('Unexpected relay failure')
                            self._record_relay_error(peer, path, exc)
            finally:
                self._relay_queue.task_done()

    def broadcast(self, path, payload, *, following=()):
        """Schedule best-effort relay; does not wait for remote HTTP replies."""
        with self.lock:
            if not self.online or not self.peers or self._relay_stop.is_set():
                return {'relay_queued': False, 'relay_errors': []}
            job = (self._network_epoch, tuple(sorted(self.peers)), ((path, payload), *following))
            try:
                self._relay_queue.put_nowait(job)
            except Full:
                error = {'peer': None, 'path': path, 'error': 'Relay queue full; synchronize explicitly'}
                self._relay_errors.append(error)
                return {'relay_queued': False, 'relay_errors': [error]}
            if self._relay_worker is None:
                self._relay_worker = Thread(target=self._relay_loop, daemon=True, name='peer-relay')
                self._relay_worker.start()
        return {'relay_queued': True, 'relay_errors': []}

    def close(self):
        """Stop taking new relay jobs; the daemon may finish an in-flight request."""
        self._relay_stop.set()
        if self._relay_worker is not None:
            self._relay_worker.join(timeout=1)

    def submit(self, tx, *, remote=False):
        with self.lock:
            if remote and not self.online:
                return {'accepted': False, 'code': 'offline'}
            result = self.chain.submit_transaction(tx)
            answer = asdict(result)
            if result.accepted:
                answer.update(self.broadcast('/peer/transactions', tx.to_dict()))
        return answer

    def receive_history(self, blocks, *, remote=False):
        with self.lock:
            if remote and not self.online:
                return {'accepted': False, 'code': 'offline'}
            result = choose_chain(self.chain, blocks)
            answer = asdict(result.outcome)
            if not result.outcome.accepted:
                return answer
            self.chain = result.chain
            snapshot = self.snapshot()
            answer.update(common_height=result.common_height,
                          orphaned=[tx.txid for tx in result.orphaned],
                          recovered=[tx.txid for tx in result.recovered],
                          dropped=list(result.dropped))
            following = tuple(('/peer/transactions', tx.to_dict()) for tx in result.recovered)
            answer.update(self.broadcast('/peer/blocks', snapshot, following=following))
        print(f'{self.address} ADOPT height={snapshot["blocks"][-1]["header"]["height"]} '
              f'common={result.common_height} recovered={len(result.recovered)}', flush=True)
        return answer

    def mine(self, address):
        # 1. Capture immutable history and pending transactions under the lock.
        with self.lock:
            history = self.chain.blocks
            pending = self.chain.pending()
            parent_hash = history[-1].hash
        # 2. Compute PoW outside the lock. Other requests can read/update state.
        timestamp = max(int(time()), history[-1].header.timestamp + 1)
        if timestamp > int(time()) + MAX_FUTURE_SECONDS:
            return {'accepted': False, 'code': 'invalid_mining_timestamp',
                    'reason': 'Next timestamp is too far in the future; retry later'}
        candidate = create_mining_candidate(history, pending, address, timestamp, POLICY)
        try:
            result = mine_block(candidate)
        except RuntimeError as exc:
            return {'accepted': False, 'code': 'mining_exhausted', 'reason': str(exc)}
        # 3. Commit only if the mined parent is still the canonical tip.
        with self.lock:
            if self.chain.tip.hash != parent_hash:
                return {'accepted': False, 'code': 'stale_candidate',
                        'reason': 'Tip changed during mining; retry on the current chain'}
            outcome = self.chain.add_block(result.block)
            if not outcome.accepted:
                return asdict(outcome)
            snapshot = self.snapshot()
            relay = self.broadcast('/peer/blocks', snapshot)
        print(f'{self.address} MINED height={result.block.header.height} hash={result.block.hash[:12]}', flush=True)
        return {'accepted': True, 'block': result.block.to_dict(), 'attempts': result.attempts, **relay}

    def synchronize(self):
        with self.lock:
            peers = sorted(self.peers) if self.online else []
        results = []
        for peer in peers:
            try:
                data = request(peer + '/peer/chain')
                result = self.receive_history(decode_history(data), remote=True)
                results.append({'peer': peer, **result})
            except PEER_ERRORS as exc:
                # A malformed or unreachable peer must not abort other attempts.
                results.append({'peer': peer, 'accepted': False, 'code': 'peer_error', 'reason': str(exc)})
        return {'results': results, 'status': self.status()}


class RequestError(ValueError):
    def __init__(self, code, reason, status=400):
        self.code, self.status = code, status
        super().__init__(reason)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    @property
    def node(self):
        return self.server.node

    def respond(self, data, status=200):
        raw = json.dumps(data, allow_nan=False).encode('utf-8')
        if len(raw) > MAX_BYTES:
            status = 413
            raw = b'{"accepted":false,"code":"response_too_large"}'
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            # Client disconnected or timed out while receiving the answer.
            self.close_connection = True

    def check_headers(self):
        # Exact loopback authority prevents a rebinding hostname from
        # reaching this local API. The API is for Python/CLI, not web pages.
        expected_host = self.node.address.removeprefix('http://')
        if self.headers.get_all('Host') != [expected_host]:
            raise RequestError('invalid_host', 'Expected the exact local node Host', 403)
        if self.headers.get_all('Origin') is not None:
            raise RequestError('forbidden_origin', 'Browser origins are not allowed', 403)

    def body(self):
        if (len(self.headers.get_all('Content-Type') or []) != 1
                or self.headers.get_content_type() != 'application/json'):
            raise RequestError('unsupported_media_type', 'Expected application/json', 415)
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise RequestError('bad_request', 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length') or []
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]):
            raise RequestError('bad_request', 'Expected one valid Content-Length')
        try:
            length = int(lengths[0])
        except ValueError as exc:
            raise RequestError('bad_request', 'Invalid Content-Length') from exc
        if not 0 < length <= MAX_BYTES:
            raise RequestError('invalid_size', 'Invalid request size', 413 if length > MAX_BYTES else 400)
        try:
            raw = self.rfile.read(length)
        except TimeoutError as exc:
            raise RequestError('request_timeout', 'Timed out reading request body', 408) from exc
        if len(raw) != length:
            raise RequestError('bad_request', 'Incomplete request body')
        data = self.parse_input(decode_json, raw)
        if type(data) is not dict:
            raise RequestError('bad_request', 'Expected JSON object')
        return data

    @staticmethod
    def parse_input(parser, *args):
        # Only errors raised while parsing/validating untrusted input are 400.
        try:
            return parser(*args)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise RequestError('bad_request', str(exc)) from exc

    def _dispatch(self, method):
        try:
            self.check_headers()
            if method == 'GET':
                self.get_route()
            elif method == 'POST':
                self.post_route(self.body())
            else:
                raise RequestError('method_not_allowed', 'No CORS preflight or OPTIONS API', 405)
        except RequestError as exc:
            self.close_connection = True
            self.respond({'accepted': False, 'code': exc.code, 'reason': str(exc)}, exc.status)
        except ChainValidationError as exc:
            self.respond(asdict(exc.result), 400)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            LOG.exception('Unexpected HTTP handler failure')
            self.respond({'accepted': False, 'code': 'internal_error'}, 500)

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def do_OPTIONS(self):
        self._dispatch('OPTIONS')

    def get_route(self):
        if self.path == '/status':
            self.respond(self.node.status())
        elif self.path == '/chain':
            self.respond(self.node.snapshot())
        elif re.fullmatch(r'/balance/[0-9a-f]{64}', self.path):
            address = self.path.rsplit('/', 1)[1]
            with self.node.lock:
                ledger = self.node.chain.ledger
                data = {'address': address, 'balance': ledger.balance(address),
                        'next_nonce': ledger.next_nonce(address)}
            self.respond(data)
        elif self.path == '/peer/chain':
            with self.node.lock:
                online = self.node.online
                data = self.node.snapshot() if online else {'code': 'offline'}
            self.respond(data, 200 if online else 503)
        else:
            self.respond({'code': 'not_found'}, 404)

    def post_route(self, data):
        if self.path in ('/transactions', '/peer/transactions'):
            tx = self.parse_input(Transaction.from_dict, data)
            answer = self.node.submit(tx, remote=self.path.startswith('/peer/'))
        elif self.path == '/peer/blocks':
            blocks = self.parse_input(decode_history, data)
            answer = self.node.receive_history(blocks, remote=True)
        elif self.path == '/mine':
            miner = data.get('miner')
            if not isinstance(miner, str) or not re.fullmatch(r'[0-9a-f]{64}', miner):
                raise RequestError('bad_request', 'Invalid miner address')
            answer = self.node.mine(miner)
        elif self.path == '/sync':
            answer = self.node.synchronize()
        elif self.path == '/network':
            if type(data.get('online')) is not bool:
                raise RequestError('bad_request', 'online must be boolean')
            answer = self.node.set_online(data['online'])
        elif self.path == '/peers':
            if set(data) != {'peer'}:
                raise RequestError('bad_request', 'Expected a peer field')
            self.parse_input(self.node.add_peer, data['peer'])
            answer = {'accepted': True, 'peers': self.node.status()['peers']}
        else:
            self.respond({'code': 'not_found'}, 404)
            return
        self.respond(answer)

    def log_message(self, format, *args):
        pass


def serve(port, peers=()):
    node = Node(f'http://127.0.0.1:{port}', peers)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.node = node
    print(f'Node ready: {node.address}; peers={sorted(node.peers)}', flush=True)
    try:
        server.serve_forever()
    finally:
        node.close()
        server.server_close()
