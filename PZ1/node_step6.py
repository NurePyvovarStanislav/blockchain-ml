import argparse
from blockchain.network import serve

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='PZ1 Step 6 local peer')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--peer', action='append', default=[])
    args = parser.parse_args()
    serve(args.port, args.peer)
