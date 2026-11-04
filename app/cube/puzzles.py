"""Puzzle (scramble) generation for the three test sets of the assignment."""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .engine import AXIS, ALL_MOVES, Cube, invert_sequence
from .solver import OptimalSolver


@dataclass(frozen=True)
class TestSet:
    key: str
    label: str
    min_turns: int
    max_turns: int
    default_count: int
    purpose: str
    exact_distance: Optional[int] = None  # if set, the puzzle is exactly this many moves from solved (checked by BFS)


TEST_SETS: Dict[str, TestSet] = {
    "teacher": TestSet("teacher", "Teacher setup", 3, 3, 1,
                       "One cube exactly 3 moves from solved (the real OpenRouter experiment)", exact_distance=3),
    "warmup": TestSet("warmup", "Warm-up", 3, 4, 3, "Verify adapters, move validation and the cube engine"),
    "primary": TestSet("primary", "Primary benchmark", 4, 5, 10, "Compare models where the exact shortest distance is known"),
    "extension": TestSet("extension", "Extension", 15, 20, 5, "Harder states: look at failure rates"),
}


@dataclass
class Puzzle:
    id: str
    index: int
    test_set: str
    seed: str
    scramble: List[str]
    facelets: str
    reference_moves: int  # exact optimum, or an upper bound (see reference_kind)
    reference_kind: str  # "exact" | "upper_bound"
    reference_solution: List[str] = field(default_factory=list)

    @property
    def scramble_len(self) -> int:
        return len(self.scramble)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scramble_len"] = self.scramble_len
        return d


def random_scramble(length: int, rng: random.Random) -> List[str]:
    """Random scramble of ``length`` moves. Two moves in a row never use the same axis.

    That keeps every move useful (no 'U D U' that could be written shorter).
    """
    moves: List[str] = []
    last_axis: Optional[int] = None
    while len(moves) < length:
        move = rng.choice(ALL_MOVES)
        if AXIS[move[0]] == last_axis:
            continue
        moves.append(move)
        last_axis = AXIS[move[0]]
    return moves


def _kociemba_solution(facelets: str) -> Optional[List[str]]:
    """Optional near-optimal reference for long scrambles (needs ``pip install kociemba``)."""
    try:
        import kociemba  # type: ignore

        return kociemba.solve(facelets).split()
    except Exception:
        return None


def make_puzzle(test_set: str, base_seed: int, index: int, solver: OptimalSolver) -> Puzzle:
    spec = TEST_SETS[test_set]
    attempt = 0
    while True:
        rng = random.Random(f"{base_seed}:{test_set}:{index}:{attempt}")
        length = rng.randint(spec.min_turns, spec.max_turns)
        scramble = random_scramble(length, rng)
        cube = Cube.from_moves(scramble)
        if not cube.is_solved():
            if spec.exact_distance is None or solver.distance(cube.facelets) == spec.exact_distance:
                break
        attempt += 1

    exact = solver.solve(cube.facelets) if length <= solver.max_exact_depth else None
    if exact is not None:
        ref_moves, ref_kind, ref_solution = len(exact), "exact", exact
    else:
        ref_solution = invert_sequence(scramble)
        ref_moves, ref_kind = length, "upper_bound"
        koc = _kociemba_solution(cube.facelets)
        if koc is not None and len(koc) < ref_moves:
            ref_moves, ref_solution = len(koc), koc

    return Puzzle(
        id=f"{test_set}-{index + 1:02d}",
        index=index,
        test_set=test_set,
        seed=f"{base_seed}:{test_set}:{index}:{attempt}",
        scramble=scramble,
        facelets=cube.facelets,
        reference_moves=ref_moves,
        reference_kind=ref_kind,
        reference_solution=ref_solution,
    )


def make_puzzles(test_set: str, count: int, base_seed: int, solver: OptimalSolver) -> List[Puzzle]:
    return [make_puzzle(test_set, base_seed, i, solver) for i in range(count)]
