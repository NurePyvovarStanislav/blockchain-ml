"""Step 4: deterministic educational PoW difficulty adjustments.

Difficulty = number of leading zero HEX digits in the SHA-256 block hash.

Retargeting occurs FOR THE NEXT BLOCK after every K already-mined blocks:
with genesis at height 0 and K=10, the first retargeted block is height 11.
For height 11, measure timestamps of blocks 0 and 10 => 10 intervals.

The previous chain is assumed trusted/validated by its caller. Full history
validation, bounded miner-controlled timestamps and forks belong to Steps 5/6.
"""

from dataclasses import dataclass
from collections.abc import Sequence

from .block import Block


@dataclass(frozen=True)
class MiningPolicy:
    fixed_subsidy: int = 50
    initial_difficulty: int = 2
    target_block_seconds: int = 10
    retarget_interval: int = 10
    min_difficulty: int = 1
    max_difficulty: int = 4

    def __post_init__(self) -> None:
        if type(self.fixed_subsidy) is not int or self.fixed_subsidy < 1:
            raise ValueError("fixed_subsidy must be a positive integer")
        for name in ("initial_difficulty", "target_block_seconds", "retarget_interval", "min_difficulty", "max_difficulty"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not (1 <= self.min_difficulty <= self.initial_difficulty <= self.max_difficulty <= 64):
            raise ValueError("Difficulty limits must satisfy 1 <= min <= initial <= max <= 64")

    def expected_next_difficulty(self, history: Sequence[Block]) -> int:
        """Derive REQUIRED difficulty solely from the accepted prefix.

        `history` begins with genesis (height=0) and contains sequential blocks.
        It must already be verified by the chain layer (implemented in Step 5).
        """
        if not history:
            raise ValueError("History must contain the common genesis block")
        if any(not isinstance(block, Block) for block in history):
            raise TypeError("History must contain Block objects")
        if history[0].header.height != 0:
            raise ValueError("History must start at genesis height 0")
        if history[-1].header.height != len(history) - 1:
            raise ValueError("History must contain a continuous sequence of heights")
        next_height = len(history)
        if next_height == 1:
            return self.initial_difficulty

        previous_difficulty = history[-1].header.difficulty
        if not self.min_difficulty <= previous_difficulty <= self.max_difficulty:
            raise ValueError("Previous difficulty is outside configured bounds")

        # 10 normal blocks have already been created at heights 1..10.
        # The 11th normal block (height=11) is the first adjustment.
        if (next_height - 1) % self.retarget_interval != 0:
            return previous_difficulty

        window_start = history[-(self.retarget_interval + 1)]
        window_end = history[-1]
        elapsed = window_end.header.timestamp - window_start.header.timestamp
        if elapsed <= 0:
            raise ValueError("Retarget window must have increasing timestamps")
        expected = self.retarget_interval * self.target_block_seconds

        # One whole HEX digit = x16 expected work; deliberately conservative.
        if elapsed * 2 < expected:
            return min(previous_difficulty + 1, self.max_difficulty)
        if elapsed > expected * 2:
            return max(previous_difficulty - 1, self.min_difficulty)
        return previous_difficulty
