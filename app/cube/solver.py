"""Exact shortest-solution finder (used to get the true optimum for small puzzles).

Method: meet in the middle. Breadth-first search (BFS) is a complete shortest
path method, so the distance it finds is exact under the half-turn metric.

1. Search from the SOLVED cube down to depth ``table_depth`` (default 4) once,
   and remember how to get back from each state (about 47 thousand states).
2. For a given puzzle, search from the puzzle layer by layer. As soon as a
   state of the puzzle search is found in the table, we know a full solution.
   We keep searching until no shorter one can exist.

With table depth 4 and a forward search of depth 4 this proves the exact
distance for every puzzle whose shortest solution has at most 8 moves. The
primary benchmark (4-5 random turns) is always inside that range.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .engine import ALL_MOVES, SOLVED, apply_move_str, invert_sequence, validate_facelets


class OptimalSolver:
    def __init__(self, table_depth: int = 4) -> None:
        self.table_depth = table_depth
        self._table: Optional[Dict[str, Tuple[str, ...]]] = None

    # ------------------------------------------------------------------ table
    @property
    def table(self) -> Dict[str, Tuple[str, ...]]:
        if self._table is None:
            self._table = self._build_table(self.table_depth)
        return self._table

    @staticmethod
    def _build_table(depth: int) -> Dict[str, Tuple[str, ...]]:
        table: Dict[str, Tuple[str, ...]] = {SOLVED: ()}
        frontier: List[Tuple[str, Tuple[str, ...]]] = [(SOLVED, ())]
        for _ in range(depth):
            nxt: List[Tuple[str, Tuple[str, ...]]] = []
            for state, seq in frontier:
                last = seq[-1][0] if seq else None
                for move in ALL_MOVES:
                    if move[0] == last:  # never turn the same face twice in a row
                        continue
                    new_state = apply_move_str(state, move)
                    if new_state in table:
                        continue
                    new_seq = seq + (move,)
                    table[new_state] = new_seq
                    nxt.append((new_state, new_seq))
            frontier = nxt
        return table

    # ------------------------------------------------------------------ solve
    @property
    def max_exact_depth(self) -> int:
        """Largest distance this solver can prove (table depth + search depth)."""
        return self.table_depth * 2

    def solve(self, facelets: str, max_depth: Optional[int] = None) -> Optional[List[str]]:
        """Return a shortest solution (list of moves) or ``None`` if it is longer than ``max_depth``."""
        validate_facelets(facelets)
        limit = self.max_exact_depth if max_depth is None else min(max_depth, self.max_exact_depth)
        table = self.table

        if facelets in table:
            return invert_sequence(table[facelets])

        best_len: Optional[int] = None
        best_solution: Optional[List[str]] = None
        visited = {facelets}
        frontier: List[Tuple[str, Tuple[str, ...]]] = [(facelets, ())]
        for depth in range(1, self.table_depth + 1):
            nxt: List[Tuple[str, Tuple[str, ...]]] = []
            for state, seq in frontier:
                last = seq[-1][0] if seq else None
                for move in ALL_MOVES:
                    if move[0] == last:
                        continue
                    new_state = apply_move_str(state, move)
                    if new_state in visited:
                        continue
                    visited.add(new_state)
                    new_seq = seq + (move,)
                    back = table.get(new_state)
                    if back is not None:
                        total = depth + len(back)
                        if total <= limit and (best_len is None or total < best_len):
                            best_len = total
                            best_solution = list(new_seq) + invert_sequence(back)
                    nxt.append((new_state, new_seq))
            frontier = nxt
            # a later layer can never give fewer than depth+1 moves
            if best_len is not None and best_len <= depth + 1:
                break
        return best_solution

    def distance(self, facelets: str, max_depth: Optional[int] = None) -> Optional[int]:
        solution = self.solve(facelets, max_depth)
        return None if solution is None else len(solution)
