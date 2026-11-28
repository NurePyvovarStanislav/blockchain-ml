import argparse
import os
import sys
from threading import Thread

from blockchain.network import serve


def stop_when_parent_closes_pipe():
    # Demo-only: the real interpreter can outlive the Windows venv launcher.
    # EOF means its parent is gone; this node has no persistent resources.
    try:
        sys.stdin.buffer.read(1)
    finally:
        os._exit(0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='PZ1 Step 6 local peer')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--peer', action='append', default=[])
    parser.add_argument('--demo-instance', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.demo_instance is not None:
        Thread(target=stop_when_parent_closes_pipe, daemon=True).start()
    serve(args.port, args.peer, instance_id=args.demo_instance)
