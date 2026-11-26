"""Live three-process demonstration. Ctrl+C stops and cleans up child nodes."""
import argparse
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep

from blockchain.network import request
from blockchain.wallet import Wallet
from blockchain.transaction import create_signed_transaction


def main(base_port=8101, pause=False):
    urls = [f'http://127.0.0.1:{base_port+i}' for i in range(3)]
    children = []
    def get(i, path='/status'):
        return request(urls[i] + path)
    def post(i, path, payload):
        return request(urls[i] + path, payload, timeout=20)
    def accepted(i, path, payload):
        result = post(i, path, payload)
        if not result.get('accepted'):
            raise RuntimeError(result)
        return result
    def wait_for(condition, message, timeout=20):
        # A successful POST now means local acceptance, not delivery to peers.
        deadline = monotonic() + timeout
        while True:
            statuses = [get(i) for i in range(3)]
            if condition(statuses):
                return
            if monotonic() >= deadline:
                raise RuntimeError(f'{message}: {statuses}')
            sleep(0.05)
    def show(title):
        print('\n' + title, flush=True)
        statuses = [get(i) for i in range(3)]
        for label, status in zip('ABC', statuses):
            print(f"{label} :{status['node'].rsplit(':',1)[1]} online={status['online']} "
                  f"height={status['height']} work={status['work']} tip={status['tip'][:12]} "
                  f"pending={[x[:12] for x in status['pending']]}", flush=True)
        if pause:
            input('Enter to continue... ')
        return statuses
    def same(statuses):
        return len({s['tip'] for s in statuses}) == 1
    def balance(i, wallet):
        return get(i, '/balance/' + wallet.address)['balance']
    def check(condition, msg):
        if not condition:
            raise RuntimeError(msg)
    try:
        for i, url in enumerate(urls):
            args = [sys.executable, '-u', str(Path(__file__).with_name('node_step6.py')),
                    '--port', str(base_port+i)]
            for peer in urls:
                if peer != url:
                    args += ['--peer', peer]
            children.append(subprocess.Popen(args))
        deadline = monotonic() + 15
        for i in range(3):
            while True:
                if children[i].poll() is not None or monotonic() > deadline:
                    raise RuntimeError('Node failed to start')
                try:
                    if get(i).get('node') != urls[i]:
                        raise RuntimeError('Port is already occupied by another node')
                    break
                except OSError:
                    sleep(0.05)
        print('\nProcess IDs:', [p.pid for p in children], flush=True)
        check(same(show('1. COMMON GENESIS')), 'Different genesis')
        alice, bob, charlie = Wallet.generate(), Wallet.generate(), Wallet.generate()
        accepted(0, '/mine', {'miner': alice.address})
        wait_for(lambda s: same(s) and all(x['height'] == 1 for x in s), 'Block 1 did not propagate')
        tx0 = create_signed_transaction(alice, bob.address, 5, 1, 0)
        accepted(0, '/transactions', tx0.to_dict())
        wait_for(lambda s: all(tx0.txid in x['pending'] for x in s), 'Transaction did not propagate')
        s = show('2. TRANSACTION PROPAGATION')
        check(all(tx0.txid in x['pending'] for x in s), 'Transaction did not propagate')
        accepted(0, '/mine', {'miner': alice.address})
        wait_for(lambda s: same(s) and all(x['height'] == 2 and not x['pending'] for x in s), 'Block 2 did not propagate')
        s = show('3. COMMON BLOCK 2')
        check(same(s) and all(not x['pending'] for x in s), 'Block did not propagate')
        check(all(balance(i, alice) == 95 for i in range(3)), 'Wrong balance')
        post(2, '/network', {'online': False})
        orphan = create_signed_transaction(alice, bob.address, 20, 2, 1)
        accepted(2, '/transactions', orphan.to_dict())
        accepted(2, '/mine', {'miner': charlie.address})
        accepted(0, '/mine', {'miner': bob.address})
        wait_for(lambda s: s[0]['tip'] == s[1]['tip'] != s[2]['tip'] and all(x['height'] == 3 for x in s), 'Expected fork did not form')
        s = show('4. PARTITION: TWO VALID TIPS OF EQUAL WORK')
        check(s[0]['tip'] == s[1]['tip'] != s[2]['tip'] and s[0]['work'] == s[2]['work'], 'Expected fork')
        print('Alice balance A/C:', balance(0, alice), balance(2, alice), flush=True)
        accepted(0, '/mine', {'miner': bob.address})
        wait_for(lambda s: s[0]['tip'] == s[1]['tip'] and s[0]['height'] == 4 and s[2]['height'] == 3, 'Expected stronger branch')
        s = show('5. A/B BRANCH HAS MORE WORK')
        check(s[0]['work'] > s[2]['work'], 'Expected stronger branch')
        post(2, '/network', {'online': True})
        result = post(2, '/sync', {})
        print('C synchronization:', [(r['code'], len(r.get('recovered', []))) for r in result['results']], flush=True)
        wait_for(lambda s: same(s) and all(orphan.txid in x['pending'] for x in s), 'Reorg or orphan recovery failed')
        s = show('6. REORG: SAME TIP, ORPHANED TRANSFER BACK IN MEMPOOLS')
        check(same(s) and all(orphan.txid in x['pending'] for x in s), 'Reorg or orphan recovery failed')
        print('Alice balance A/B/C:', [balance(i, alice) for i in range(3)], flush=True)
        print('Charlie lost orphaned coinbase:', balance(2, charlie), flush=True)
        check(balance(2, charlie) == 0, 'Orphaned reward survived')
        accepted(1, '/mine', {'miner': bob.address})
        wait_for(lambda s: same(s) and all(x['height'] == 5 and not x['pending'] for x in s), 'Final convergence failed')
        s = show('7. RECOVERED TRANSFER CONFIRMED ON WINNING BRANCH')
        check(same(s) and all(not x['pending'] for x in s), 'Final convergence failed')
        for label, wallet in [('Alice', alice), ('Bob', bob), ('Charlie', charlie)]:
            print(label, 'balances A/B/C:', [balance(i, wallet) for i in range(3)], flush=True)
        check(all(balance(i, alice) == 73 and balance(i, bob) == 177 and balance(i, charlie) == 0
                  for i in range(3)), 'Incorrect final balances')
        print('PASS: three independent processes converged.', flush=True)
    finally:
        for p in children:
            if p.poll() is None:
                p.terminate()
        for p in children:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-port', type=int, default=8101)
    parser.add_argument('--pause', action='store_true')
    args = parser.parse_args()
    main(args.base_port, args.pause)
