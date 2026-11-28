"""Monte Carlo illustration of a private competing branch in the local model."""
import argparse
import math
import random
from pathlib import Path

from benchmark_step8 import write_csv


def simulate(q, trials=10_000, horizon=200, deficit=3, seed=2026):
    """Lag starts at deficit; success requires strictly more work; equal difficulty."""
    if not 0 <= q <= 1 or trials < 1 or horizon < 1 or deficit < 0:
        raise ValueError("Invalid simulation parameters")
    rng = random.Random(seed)
    successes = 0
    for _ in range(trials):
        lag = deficit
        for _ in range(horizon):
            lag += -1 if rng.random() < q else 1
            if lag < 0:
                successes += 1
                break
    # Wilson interval remains defined even at observed frequency 0 or 1.
    p = successes / trials
    z = 1.959963984540054
    den = 1 + z*z/trials
    center = (p + z*z/(2*trials))/den
    half = z*math.sqrt(p*(1-p)/trials + z*z/(4*trials*trials))/den
    return {"share": q, "trials": trials, "horizon": horizon, "initial_deficit": deficit,
            "successes": successes, "probability": p,
            "ci_low": max(0, center-half), "ci_high": min(1, center+half),
            "eventual_theory": (q/(1-q))**(deficit+1) if q < .5 else 1.0}


def plot(rows, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x = [100*r["share"] for r in rows]
    ys = [100*r["probability"] for r in rows]
    bars = [[100*(r["probability"]-r["ci_low"]) for r in rows],
            [100*(r["ci_high"]-r["probability"]) for r in rows]]
    fig, ax = plt.subplots(figsize=(8.3,4.8), layout="constrained")
    ax.errorbar(x, ys, yerr=bars, marker="o", capsize=3, lw=2, color="#285a82",
                label=f"Симуляція, {rows[0]['horizon']} відкритих блоків")
    ax.plot(x,[100*r["eventual_theory"] for r in rows],"--",color="#b77929",
            label="Теорія, необмежений час")
    ax.axvline(50, color="#777777", linestyle=":", label="50 % потужності")
    ax.set(xlabel="Частка обчислювальної потужності атакувальника, %",
           ylabel="Імовірність випередження, %", ylim=(-2,102),
           xticks=[10,20,30,40,50,60,70])
    ax.grid(alpha=.23)
    ax.legend(loc="upper left")
    fig.savefig(path,dpi=200)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trials", type=int, default=10_000)
    p.add_argument("--horizon", type=int, default=200)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--out", type=Path, default=Path("report_assets"))
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    shares = [.10,.20,.30,.40,.45,.49,.50,.51,.55,.60,.70]
    rows = [simulate(q, a.trials, a.horizon, 3, a.seed+i) for i,q in enumerate(shares)]
    write_csv(a.out/"step8_majority.csv",rows)
    plot(rows,a.out/"step8_majority_probability.png")
    print(f"Initial lag=3, strict lead required, trials={a.trials}, horizon={a.horizon}, seed={a.seed}")
    for r in rows:
        print(f"q={r['share']:.0%}: {r['successes']}/{r['trials']} = {r['probability']:.2%}, "
              f"eventual theory={r['eventual_theory']:.2%}")
    print("Saved majority simulation CSV and graph.")


if __name__ == "__main__":
    main()
