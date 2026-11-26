"""Regression tests for the Step 6 network review (also runnable without pytest)."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import BadStatusLine, HTTPConnection
from http.server import ThreadingHTTPServer
from queue import Queue
from threading import Event, Thread
from unittest.mock import patch
from urllib.error import URLError

from blockchain import network
from blockchain.chain import Blockchain
from blockchain.config import NETWORK_ID
from blockchain.mining import mine_block
from blockchain.network import Handler, Node, request
from blockchain.transaction import create_signed_transaction
from blockchain.wallet import Wallet


class FastTimeoutHandler(Handler):
    def setup(self):
        super().setup()
        self.connection.settimeout(0.25)


class NetworkReviewTests(unittest.TestCase):
    def setUp(self):
        # Actual loopback HTTP; ephemeral ports avoid interference with other nodes.
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FastTimeoutHandler)
        self.port = self.server.server_port
        self.node = Node(f'http://127.0.0.1:{self.port}')
        self.server.node = self.node
        self.server_thread = Thread(target=self.server.serve_forever,
                                    kwargs={'poll_interval': 0.02}, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.shutdown_server)
        self.alice, self.bob = Wallet.generate(), Wallet.generate()

    def shutdown_server(self):
        self.server.shutdown()
        self.node.close()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def http(self, method, path, body=None, **headers):
        conn = HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def post(self, path, payload):
        return self.http('POST', path, json.dumps(payload),
                         **{'Content-Type': 'application/json'})

    def test_status_is_available_while_mining(self):
        started, release = Event(), Event()

        def slow(candidate):
            started.set()
            if not release.wait(3):
                raise AssertionError('Test did not release simulated PoW')
            return mine_block(candidate)

        # Read actual HTTP while the first POST is waiting in the PoW search.
        with patch.object(network, 'mine_block', slow):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(request, self.node.address + '/mine',
                                         {'miner': self.alice.address})
                try:
                    self.assertTrue(started.wait(2))
                    self.assertEqual(self.http('GET', '/status')[1]['height'], 0)
                    balance = self.http('GET', '/balance/' + self.alice.address)
                    self.assertEqual((balance[0], balance[1]['balance']), (200, 0))
                    self.assertFalse(future.done())
                finally:
                    release.set()
                self.assertTrue(future.result(timeout=3)['accepted'])
        self.assertEqual(self.node.status()['height'], 1)

    def test_stale_candidate_after_remote_chain_adoption(self):
        started, release = Event(), Event()
        winning = Blockchain()
        winning.mine_pending(self.bob.address)

        def slow(candidate):
            started.set()
            if not release.wait(3):
                raise AssertionError('Test did not release simulated PoW')
            return mine_block(candidate)

        with patch.object(network, 'mine_block', slow):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.node.mine, self.alice.address)
                try:
                    self.assertTrue(started.wait(2))
                    self.assertTrue(self.node.receive_history(winning.blocks, remote=True)['accepted'])
                finally:
                    release.set()
                result = future.result(timeout=3)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['code'], 'stale_candidate')
        self.assertEqual(self.node.chain.tip, winning.tip)
        self.assertEqual(self.node.chain.balance(self.alice.address), 0)
        self.assertEqual(self.node.chain.balance(self.bob.address), 50)

    def test_transaction_arriving_during_pow_remains_pending(self):
        self.assertTrue(self.node.mine(self.alice.address)['accepted'])
        tx = create_signed_transaction(self.alice, self.bob.address, 10, 1, 0)
        started, release = Event(), Event()

        def slow(candidate):
            started.set()
            if not release.wait(3):
                raise AssertionError('Test did not release simulated PoW')
            return mine_block(candidate)

        with patch.object(network, 'mine_block', slow):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.node.mine, self.alice.address)
                try:
                    self.assertTrue(started.wait(2))
                    self.assertTrue(self.node.submit(tx)['accepted'])
                finally:
                    release.set()
                self.assertTrue(future.result(timeout=3)['accepted'])
        self.assertEqual(self.node.chain.pending(), (tx,))
        self.assertFalse(self.node.chain.ledger.was_confirmed(tx.txid))

    def test_mining_exhaustion_keeps_state(self):
        with patch.object(network, 'mine_block', side_effect=RuntimeError('No solution')):
            response = self.post('/mine', {'miner': self.alice.address})
        self.assertEqual((response[0], response[1]['code']), (200, 'mining_exhausted'))
        self.assertEqual(self.node.status()['height'], 0)

    def test_bad_host_rejected_for_get(self):
        response = self.http('GET', '/status', Host=f'evil.example:{self.port}')
        self.assertEqual((response[0], response[1]['code']), (403, 'invalid_host'))

    def test_bad_host_rejected_for_post(self):
        response = self.http('POST', '/network', '{"online":false}',
                             Host=f'evil.example:{self.port}',
                             **{'Content-Type': 'application/json'})
        self.assertEqual((response[0], response[1]['code']), (403, 'invalid_host'))
        self.assertTrue(self.node.online)

    def test_browser_origin_rejected(self):
        for origin in ('https://evil.example', 'null'):
            response = self.http('POST', '/network', '{"online":false}',
                                 Origin=origin, **{'Content-Type': 'application/json'})
            self.assertEqual((response[0], response[1]['code']), (403, 'forbidden_origin'))
            self.assertTrue(self.node.online)

    def test_plain_text_post_rejected(self):
        response = self.http('POST', '/network', '{"online":false}',
                             **{'Content-Type': 'text/plain'})
        self.assertEqual((response[0], response[1]['code']), (415, 'unsupported_media_type'))
        self.assertTrue(self.node.online)

    def test_json_with_charset_accepted(self):
        response = self.http('POST', '/network', '{"online":false}',
                             **{'Content-Type': 'application/json; charset=utf-8'})
        self.assertEqual(response[0], 200)
        self.assertFalse(self.node.online)

    def test_preflight_rejected(self):
        response = self.http('OPTIONS', '/mine', Origin='https://evil.example',
                             **{'Access-Control-Request-Method': 'POST'})
        self.assertEqual((response[0], response[1]['code']), (403, 'forbidden_origin'))

    def test_body_timeout_returns_408(self):
        # Promise more bytes than we send; keep socket open until server timeout.
        response = self.http('POST', '/network', '{',
                             **{'Content-Type': 'application/json',
                                'Content-Length': '100'})
        self.assertEqual((response[0], response[1]['code']), (408, 'request_timeout'))
        self.assertTrue(self.node.online)

    def test_invalid_json_returns_400(self):
        response = self.http('POST', '/network', '{"online":true,"online":false}',
                             **{'Content-Type': 'application/json'})
        self.assertEqual((response[0], response[1]['code']), (400, 'bad_request'))
        self.assertTrue(self.node.online)

    def test_bad_miner_returns_400(self):
        response = self.post('/mine', {'miner': 'not-an-address'})
        self.assertEqual((response[0], response[1]['code']), (400, 'bad_request'))

    def test_unexpected_get_error_returns_500(self):
        with patch.object(self.node, 'status', side_effect=AttributeError('private details')):
            with self.assertLogs(network.LOG, level='ERROR'):
                response = self.http('GET', '/status')
        self.assertEqual(response, (500, {'accepted': False, 'code': 'internal_error'}))
        self.assertEqual(self.http('GET', '/status')[0], 200)

    def test_unexpected_post_error_returns_500(self):
        with patch.object(self.node, 'mine', side_effect=KeyError('private details')):
            with self.assertLogs(network.LOG, level='ERROR'):
                response = self.post('/mine', {'miner': self.alice.address})
        self.assertEqual(response, (500, {'accepted': False, 'code': 'internal_error'}))
        self.assertEqual(self.http('GET', '/status')[0], 200)

    def test_oversized_request_returns_413(self):
        with patch.object(network, 'MAX_BYTES', 128):
            response = self.http('POST', '/network', 'x'*129,
                                 **{'Content-Type': 'application/json'})
        self.assertEqual((response[0], response[1]['code']), (413, 'invalid_size'))

    def test_oversized_response_returns_413(self):
        with patch.object(network, 'MAX_BYTES', 128):
            response = self.http('GET', '/chain')
        self.assertEqual((response[0], response[1]['code']), (413, 'response_too_large'))

    def test_sync_continues_after_bad_peers(self):
        peers = ['http://127.0.0.1:10001', 'http://127.0.0.1:10002',
                 'http://127.0.0.1:10003', 'http://127.0.0.1:10004',
                 'http://127.0.0.1:10005']
        for p in peers:
            self.node.add_peer(p)
        good = Blockchain()
        good.mine_pending(self.alice.address)
        history = {'network': NETWORK_ID, 'blocks': [b.to_dict() for b in good.blocks]}
        responses = [KeyError('invalid block'), TypeError('invalid header'),
                     BadStatusLine('not HTTP'), URLError('offline'), history]

        def mock_request(url, payload=None):
            if payload is not None:
                return {'accepted': False, 'code': 'not_more_work'}
            response = responses[peers.index(url.removesuffix('/peer/chain'))]
            if isinstance(response, Exception):
                raise response
            return response

        with patch.object(network, 'request', side_effect=mock_request):
            answer = self.node.synchronize()
            self.node._relay_queue.join()
        self.assertEqual([r['code'] for r in answer['results']],
                         ['peer_error']*4 + ['adopted_chain'])
        self.assertEqual(self.node.chain.tip, good.tip)

    def test_sync_continues_after_structurally_invalid_history(self):
        bad, good = 'http://127.0.0.1:10001', 'http://127.0.0.1:10002'
        for peer in (bad, good):
            self.node.add_peer(peer)
        winning = Blockchain()
        winning.mine_pending(self.alice.address)

        def mock_request(url, payload=None):
            if payload is not None:
                return {'accepted': False, 'code': 'not_more_work'}
            if url.startswith(bad):
                return {'network': NETWORK_ID, 'blocks': [{'header': None}]}
            return {'network': NETWORK_ID, 'blocks': [b.to_dict() for b in winning.blocks]}

        with patch.object(network, 'request', side_effect=mock_request):
            answer = self.node.synchronize()
            self.node._relay_queue.join()
        self.assertEqual([r['code'] for r in answer['results']], ['peer_error', 'adopted_chain'])

    def test_relay_is_async_and_ordered(self):
        peer = 'http://127.0.0.1:10001'
        self.node.add_peer(peer)
        started, release = Event(), Event()
        sent = []

        def slow_request(url, payload):
            sent.append(url)
            if url.endswith('/peer/blocks'):
                started.set()
                if not release.wait(3):
                    raise AssertionError('Test did not release relay')
            return {'accepted': True}

        with patch.object(network, 'request', side_effect=slow_request):
            try:
                answer = self.node.broadcast('/peer/blocks', {}, following=(('/peer/transactions', {}),))
                self.assertTrue(answer['relay_queued'])
                self.assertTrue(started.wait(2))
                self.assertEqual(sent, [peer + '/peer/blocks'])
                self.assertEqual(self.http('GET', '/status')[0], 200)
            finally:
                release.set()
                self.node._relay_queue.join()
        self.assertEqual(sent, [peer + '/peer/blocks', peer + '/peer/transactions'])

    def test_full_queue_does_not_undo_local_acceptance(self):
        self.node.add_peer('http://127.0.0.1:10001')
        self.node._relay_queue = Queue(maxsize=1)
        started, release = Event(), Event()

        def slow_request(url, payload):
            started.set()
            release.wait(3)
            return {'accepted': True}

        with patch.object(network, 'request', side_effect=slow_request):
            try:
                self.node.broadcast('/peer/blocks', {})
                self.assertTrue(started.wait(2))
                self.node.broadcast('/peer/blocks', {})
                answer = self.node.mine(self.alice.address)
                self.assertTrue(answer['accepted'])
                self.assertFalse(answer['relay_queued'])
                self.assertIn('queue full', answer['relay_errors'][0]['error'])
                self.assertEqual(self.node.status()['height'], 1)
            finally:
                release.set()
                self.node._relay_queue.join()

    def test_async_http_error_visible_in_status(self):
        self.node.add_peer('http://127.0.0.1:10001')
        with patch.object(network, 'request', side_effect=BadStatusLine('broken HTTP')):
            answer = self.node.broadcast('/peer/blocks', {})
            self.node._relay_queue.join()
        self.assertTrue(answer['relay_queued'])
        self.assertEqual(answer['relay_errors'], [])
        self.assertEqual(self.node.status()['relay_errors'][-1]['path'], '/peer/blocks')

    def test_partition_discards_queued_old_messages(self):
        peer = 'http://127.0.0.1:10001'
        self.node.add_peer(peer)
        started, release = Event(), Event()
        sent = []

        def slow_request(url, payload):
            sent.append(payload)
            started.set()
            release.wait(3)
            return {'accepted': True}

        with patch.object(network, 'request', side_effect=slow_request):
            try:
                self.node.broadcast('/peer/blocks', {'id': 1})
                self.assertTrue(started.wait(2))
                self.node.broadcast('/peer/blocks', {'id': 2})
                self.node.set_online(False)
                self.node.set_online(True)
            finally:
                release.set()
                self.node._relay_queue.join()
        self.assertEqual(sent, [{'id': 1}])

    def test_add_peer_at_runtime_and_reject_self(self):
        peer = 'http://127.0.0.1:10001'
        status, result = self.post('/peers', {'peer': peer})
        self.assertEqual(status, 200)
        self.assertEqual(result['peers'], [peer])
        self.assertEqual(self.post('/peers', {'peer': peer})[1]['peers'], [peer])
        self.assertEqual(self.post('/peers', {'peer': self.node.address})[0], 400)
        self.assertEqual(self.post('/peers', {'peer': 'http://example.com:80'})[0], 400)
        self.assertEqual(self.node.status()['peers'], [peer])


if __name__ == '__main__':
    unittest.main()
