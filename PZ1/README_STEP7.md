# Step 7 — command-line wallet and node management

Run from `PZ1` with the existing Python virtual environment activated. No additional dependencies.

```powershell
python cli.py --help
python demo_step7.py
python -m pytest -q
```

The demonstration starts three independent nodes at 8201–8203, creates encrypted wallets in a temporary directory, adds peers at runtime, mines, submits two pending transactions, displays the ledger, independently validates the chain and synchronizes the third node. It polls asynchronous state, asserts the expected results, cleans up children and deletes the temporary demo wallets. Use `--pause` to inspect stages, or `--base-port 8301` for other ports. The demo calls the same CLI parser and dispatcher using a temporary random password supplied internally rather than typed interactively. No password or private key is printed. The demo identifies each new node by a random instance ID returned in /status rather than by comparing process IDs (Windows virtual-environment launchers can have different PIDs). A parent stdin pipe closes on exit so the server processes stop even with a launcher. Existing or stale nodes on the same port are rejected; if needed, use another --base-port.

## Manual use (PowerShell)

In separate terminals in PZ1, start three initially disconnected nodes:

```powershell
python node_step6.py --port 8101
python node_step6.py --port 8102
python node_step6.py --port 8103
```

In a fourth terminal (commands below assume the activated virtual environment):

```powershell
$A = 'http://127.0.0.1:8101'
$B = 'http://127.0.0.1:8102'
$C = 'http://127.0.0.1:8103'

$alice = (python cli.py wallet-create wallets/alice.pem | ConvertFrom-Json).address
$bob = (python cli.py wallet-create wallets/bob.pem | ConvertFrom-Json).address

python cli.py --node $A peer-add $B
python cli.py --node $B peer-add $A
python cli.py --node $A mine $alice
python cli.py --node $A balance $alice
python cli.py --node $A send wallets/alice.pem $bob 10 --fee 1
python cli.py --node $A send wallets/alice.pem $bob 15 --fee 2
python cli.py --node $A balance $alice
python cli.py --node $A balance $bob
python cli.py --node $A mine $alice
python cli.py --node $A chain --summary
python cli.py --node $A validate
python cli.py --node $C peer-add $A
python cli.py --node $A peer-add $C
python cli.py --node $C sync
python cli.py --node $C validate
python cli.py --node $C balance $bob
```

Both wallet creation commands prompt twice for a password (at least 12 characters). Sending prompts for the sender wallet password. If the wallet files already exist, use:

```powershell
$alice = (python cli.py wallet-address wallets/alice.pem | ConvertFrom-Json).address
$bob = (python cli.py wallet-address wallets/bob.pem | ConvertFrom-Json).address
```

Never put real private keys or passwords in Git, command-line arguments, screenshots or HTTP messages. The existing .gitignore excludes wallets/ and *.pem. The CLI refuses a password prompt that would echo the password instead of hiding it.

## Commands

| Command | Required arguments and options | Operation |
|---|---|---|
| wallet-create | PATH | Local encrypted Ed25519 wallet (no connection required) |
| wallet-address | PATH | Open wallet and derive public address locally |
| send | PATH RECIPIENT AMOUNT [--fee FEE] [--nonce N] | GET account summary (unless nonce supplied), sign locally, POST /transactions |
| mine | ADDRESS | POST /mine |
| chain | [--summary] | GET /chain, show all blocks or concise header summaries |
| balance | ADDRESS | GET /balance/ADDRESS |
| validate | none | GET /chain, verify decoded block hashes and replay from fixed genesis locally |
| peer-add | URL | POST /peers |
| sync | none | POST /sync; per-peer results and updated status |
| status | none | GET /status |

Global options precede the command: `--node http://127.0.0.1:PORT` (default 8101), `--timeout SECONDS` (default 120, socket timeout, not total operation deadline). All node and peer URLs are restricted to loopback HTTP. A successful CLI operation exits 0, a rejected/failed operation exits 1, bad CLI syntax exits 2. JSON is written to stdout; password prompts go through getpass. A valid chain need not be the best chain globally.

The account endpoint preserves `address`, `balance` and confirmed `next_nonce` and adds `reserved`, `available_balance` and mempool-aware `expected_nonce`. The node gathers them atomically with its state lock. Pending incoming transfers remain unavailable. A concurrent transfer may consume the nonce between reading the account and submitting a signed transaction; the node rejects the conflict. Do not blindly retry. On a response timeout/transport failure after a possible POST, the CLI indicates `outcome_unknown`; for send, the TXID and nonce are included. Query the node before retrying.

A successful send means local mempool admission, not confirmation or delivery to all peers. Synchronization is explicit, and no peers or offline status are indicated as errors; equal/lower-work histories are normal. Node histories remain in memory as in Step 6; the Step 5 save/load interface is unchanged. No browser UI, authentication, automatic retry, or new consensus rule is introduced.

Expected demonstration: pending debits 28, available Alice balance 22, nonces 0 and 1, rejection of attempted debit 23. After second block mined to Alice, balances are Alice=75, Bob=25; all three nodes eventually share the same tip of height 2. The specific hashes, addresses and elapsed time vary. 
