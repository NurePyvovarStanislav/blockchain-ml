"""Measure the actual PoW miner, then plot mean and observed min/max on log y."""
import argparse
import csv
import platform
import random
import statistics
from pathlib import Path

from blockchain.chain import create_genesis
from blockchain.difficulty import MiningPolicy
from blockchain.mining import create_mining_candidate, mine_block


def measure(runs=10, seed=2026):
    if runs < 10:
        raise ValueError("At least 10 trials per difficulty are required")
    rng = random.Random(seed)
    genesis = create_genesis()
    # Vary candidate Merkle roots independently even on repeat runs.
    # Same miner, transaction count, timestamp and implementation throughout.
    rows = []
    for d in range(1, 5):
        policy = MiningPolicy(initial_difficulty=d)
        for i in range(1, runs + 1):
            candidate = create_mining_candidate(
                (genesis,), (), "a" * 64, genesis.header.timestamp + 1,
                policy, extra_nonce=rng.getrandbits(64))
            # Generous finite bound; never discard slow trials silently.
            result = mine_block(candidate, max_attempts=10_000_000)
            rows.append({"difficulty": d, "trial": i, "attempts": result.attempts,
                         "seconds": result.elapsed_seconds})
        print(f"Difficulty {d}: {runs} trials complete", flush=True)
    return rows


def summarize(rows):
    results = []
    for d in sorted({r["difficulty"] for r in rows}):
        trials = [r for r in rows if r["difficulty"] == d]
        times = [r["seconds"] for r in trials]
        results.append({"difficulty": d, "runs": len(times),
                        "mean_seconds": statistics.mean(times),
                        "min_seconds": min(times), "max_seconds": max(times),
                        "mean_attempts": statistics.mean(r["attempts"] for r in trials),
                        "theoretical_attempts": 16 ** d})
    return results


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot(summary, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ds = [r["difficulty"] for r in summary]
    means = [r["mean_seconds"] for r in summary]
    errors = [[r["mean_seconds"] - r["min_seconds"] for r in summary],
              [r["max_seconds"] - r["mean_seconds"] for r in summary]]
    fig, ax = plt.subplots(figsize=(8.3, 4.8), layout="constrained")
    ax.errorbar(ds, means, yerr=errors, marker="o", capsize=6, lw=2,
                color="#285a82", label="Середнє; мінімум–максимум")
    ax.plot(ds, [means[0] * 16 ** (d - ds[0]) for d in ds],
            "--", color="#b77929", label="Теоретичне зростання ×16 (від рівня 1)")
    ax.set(yscale="log", xlabel="Складність (кількість початкових HEX-нулів)",
           ylabel="Час майнінгу, с (логарифмічна шкала)", xticks=ds)
    ax.grid(True, which="both", alpha=.23)
    ax.legend(loc="upper left")
    fig.savefig(path, dpi=200)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--out", type=Path, default=Path("report_assets"))
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    print(f"Python {platform.python_version()} | {platform.platform()} | seed={a.seed}")
    rows = measure(a.runs, a.seed)
    summary = summarize(rows)
    write_csv(a.out / "step8_benchmark_runs.csv", rows)
    write_csv(a.out / "step8_benchmark_summary.csv", summary)
    plot(summary, a.out / "step8_mining_time.png")
    for r in summary:
        print(f"d={r['difficulty']} n={r['runs']} avg={r['mean_seconds']:.6f}s "
              f"range=[{r['min_seconds']:.6f}, {r['max_seconds']:.6f}]s "
              f"attempts_mean={r['mean_attempts']:.1f} theory={r['theoretical_attempts']}")
    print("Saved CSV and logarithmic time graph.")


if __name__ == "__main__":
    main()
