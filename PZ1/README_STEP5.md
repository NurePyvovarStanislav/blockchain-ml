# PZ1 — Step 5: chain and validation

Merge the `PZ1` folder of this patch into the existing repository. **Replace `blockchain/state.py`**; all other files are new. No extra packages required. From `PZ1` with the existing virtual environment activated:

```powershell
python demo_step5.py --save "$env:TEMP\pz1-chain.json"
python demo_step5.py --restore "$env:TEMP\pz1-chain.json"
python -m pytest -q
```

A plain `python demo_step5.py` uses a temporary file only. The two explicit commands show an actual restart. The expected total is 176 passed (137 previous + 39 new). Addresses, hashes and runtimes vary. The demo uses historical synthetic timestamps, not measured mining intervals. The exported chain contains public transaction data, not keys or pending transactions. The wallet private keys generated in this demo are ephemeral; encrypted wallet storage remains available from Step 1.

## API

- `Blockchain()` starts from a fixed empty genesis; `Blockchain(blocks)` validates a supplied history.
- `blocks`, `tip`, `ledger` (independent snapshot), `balance(address)`, `pending()` expose accepted state.
- `submit_transaction(tx)` validates admission to the local pool.
- `mine_pending(address)` mines and accepts a local candidate; optional `timestamp`, `max_attempts`.
- `add_block(block)` checks a candidate against the accepted prefix, publishes only on success, then reconciles mempool.
- `validate()` performs full replay; standalone `validate_chain(blocks)` returns a result and `replay_chain(blocks)` returns reconstructed state or raises `ChainValidationError`.
- `save(path)` writes history atomically via a temporary file; `Blockchain.load(path)` strictly decodes and validates it. `storage.block_from_dict` decodes one block and checks its declared hash, not consensus by itself.

## Protocol decisions and limits

- Common genesis timestamp 1700000000, zero balances, no coinbase, difficulty 0. The default Step 4 policy is fixed for this network. Future time limit: local clock +120 seconds, strictly increasing timestamps. Historical genesis can skew the first retarget for a newly started real-time network; the demo uses synthetic ten-second intervals.
- Normal transactions apply in block order. Coinbase is serialized first but credited last. A valid earlier ordinary transfer in a block may fund a later one; the mempool retains its stricter policy of ignoring unconfirmed incoming funds.
- A new block is validated on a copy of state; fees settle exactly once, and rejected blocks do not mutate the accepted history, state, or pool. The block-level structural helper is reused; some checks are intentionally repeated for clarity.
- No fork choice, chain replacement, network synchronization, concurrent mutation support, automatic persistence after each append, or persistent mempool yet. JSON corruption is detected by schema, hashes and replay, but a completely valid alternative history or truncation is not intrinsically detectable without an external trusted checkpoint or network comparison. Local clock differences can temporarily affect validation; no comprehensive timestamp attack protection or resource limits.
- Reports and diagrams in `report_assets`; insert real screenshots from your local runs and retain only the final report in Word. Record AI assistance as required by the assignment.
