# Step 6 — local peer network (reviewed version)

From `PZ1` with the existing virtual environment activated:

```powershell
python demo_step6.py
python -m pytest -q
```

The demo starts three separate Python processes on ports 8101–8103, funds Alice by mining, propagates a transfer, isolates C at the peer interface, creates a fork, reconnects C, synchronizes, requeues a transfer and mines it on the winning branch. It checks all important invariants, waits for asynchronous delivery with a deadline and terminates its own children in `finally`. Use `python demo_step6.py --pause` to inspect each phase live or `--base-port 8201` if ports are occupied. No new dependencies. The full suite contains 223 test cases (verified on the Step 5 repository plus the revised patch). Run only the 24 review regression tests without pytest using:

```powershell
python -m unittest discover -s tests -p test_step6_review.py -v
```

For manual launch, open three terminals in PZ1:

```powershell
python node_step6.py --port 8101 --peer http://127.0.0.1:8102 --peer http://127.0.0.1:8103
python node_step6.py --port 8102 --peer http://127.0.0.1:8101 --peer http://127.0.0.1:8103
python node_step6.py --port 8103 --peer http://127.0.0.1:8101 --peer http://127.0.0.1:8102
```

Inspect using `Invoke-RestMethod http://127.0.0.1:8101/status`. Ctrl+C ends a manually launched node. Stop manual nodes before running the demo on those ports.

## HTTP protocol

| Method | Path | JSON body / result |
|---|---|---|
| GET | /status | Node, height, tip, work, pending TXIDs, peers, online, queue size and recent relay errors |
| GET | /chain | Network-tagged chain snapshot |
| GET | /balance/ADDRESS | Confirmed balance and next confirmed nonce |
| GET | /peer/chain | Network-tagged snapshot; HTTP 503 if offline |
| POST | /transactions | Signed transaction object |
| POST | /peer/transactions | Signed transaction object |
| POST | /peer/blocks | Network-tagged full chain snapshot |
| POST | /mine | {"miner":"ADDRESS"} |
| POST | /sync | {} |
| POST | /network | {"online":false} or true |
| POST | /peers | {"peer":"http://127.0.0.1:8102"} |

For example, add a peer dynamically (the reverse direction is independent):

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8101/peers -ContentType application/json -Body '{"peer":"http://127.0.0.1:8102"}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8101/sync -ContentType application/json -Body '{}'
```

All POST requests require `Content-Type: application/json`; Host must exactly match the node's `127.0.0.1:PORT`. Requests containing Origin are rejected; the API is intended for Python/CLI, not browsers. No CORS permission is returned. Malformed input is 400, origin/host rejection 403, body read timeout 408, oversized payload 413, unsupported content type 415, unexpected server error 500 (details logged locally). These checks mitigate browser-originated CSRF and DNS rebinding; they do not authenticate local clients.

## Concurrency and relay

Mining snapshots the accepted history and mempool under RLock, searches for PoW without the lock, and commits under RLock only if the parent hash still matches the canonical tip. If it changed, `/mine` returns `accepted:false, code:stale_candidate`; retry manually on the new chain. New transactions received during the search can remain pending for the next block. Block validation and mempool reconciliation still occur atomically under the lock.

Accepted objects are scheduled for best-effort relay on a bounded FIFO queue (64 jobs) with one background worker per node, not recursively in request handlers. A successful POST means local acceptance. `relay_queued` confirms enqueueing, not delivery; immediate `relay_errors` indicates queue failure. Later errors are recorded (last 20) in `GET /status`; `relay_queue_size` excludes the job currently being processed. A full queue does not undo acceptance. Recovery sends the winning history before recovered transactions per worker. There is no guarantee of ordering across different senders or successful delivery. Demo polls statuses before each next step. Each sync attempt yields its own result; malformed/rejected/unreachable peers do not prevent trying the next one.

`/network` disables incoming/outgoing peer communication, not local commands. Jobs queued before a partition cannot be sent after reconnection, but HTTP already in flight may finish. Add peers at runtime or startup; connections remain directed; to receive spontaneous relays both ways each side must add the other.

## Limitations

Full history transfer is intentionally simple: a snapshot costs O(n) bytes for n blocks of bounded size (or O(total serialized data) in general). Full replay must validate all blocks and transactions, and may be expensive; a 4 MiB request/response bound limits the supported history size. Oversized snapshots return explicit errors; local mining can still succeed even if relay is impossible. There is no incremental headers/blocks protocol. Full chain replay during adoption and final block validation still hold the local state lock; CPU/GIL contention and lack of cancellation of stale mining are additional limits. The relay worker contacts peers sequentially, so a slow peer can delay subsequent deliveries; queue saturation drops relay jobs. No automatic retries, periodic synchronization, authenticated peers, TLS, automatic discovery, automatic disk persistence or guaranteed delivery of standalone mempool transactions. Nodes retain history in memory; Step 5 save/load remains available. Explicit sync pulls confirmed history, not all missing pending transactions. Equal work keeps the current history until one branch gains work. No centralized consensus coordinator; the demo only orchestrates the independent nodes. Use only on loopback and do not expose to an untrusted network.
