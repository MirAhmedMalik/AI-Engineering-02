"""Deterministic 3x3x3 Rubik's Cube engine.

The cube is stored as a 54-character string (the "facelets"), using the same
order as the well known Kociemba format:

    U (9)  R (9)  F (9)  D (9)  L (9)  B (9)

Each face is read row by row, as if you look straight at that face from
outside the cube:

    U : top row is next to B,  bottom row is next to F
    R : top row is next to U,  left column is next to F
    F : top row is next to U,  left column is next to L
    D : top row is next to F,  bottom row is next to B
    L : top row is next to U,  left column is next to B
    B : top row is next to U,  left column is next to R

Each letter tells which face the sticker belongs to. So the solved cube is
``UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB``.

Move permutations are NOT typed in by hand. They are computed from 3D geometry
(every sticker has a position and a direction, and a face turn rotates the
stickers of one layer by 90 degrees). This removes a whole class of typing
mistakes. The unit tests then check the group laws (a move four times is the
identity, R U R' U' repeated 6 times is the identity, and so on).

Metric: half-turn metric (HTM). A move is one of 18 face turns: U U' U2 R R' R2
F F' F2 D D' D2 L L' L2 B B' B2. Every one of them counts as exactly 1 move.
"""
from __future__ import annotations

from dataclasses import dataclass
from operator import itemgetter
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

FACES = "URFDLB"
SOLVED = "".join(face * 9 for face in FACES)

FACE_LETTERS: List[str] = ["U", "R", "F", "D", "L", "B"]
SUFFIXES: List[str] = ["", "'", "2"]
ALL_MOVES: List[str] = [f + s for f in FACE_LETTERS for s in SUFFIXES]
OPPOSITE: Dict[str, str] = {"U": "D", "D": "U", "R": "L", "L": "R", "F": "B", "B": "F"}
AXIS: Dict[str, int] = {"U": 0, "D": 0, "R": 1, "L": 1, "F": 2, "B": 2}

_FACE_NORMAL: Dict[str, Tuple[int, int, int]] = {
    "U": (0, 1, 0),
    "R": (1, 0, 0),
    "F": (0, 0, 1),
    "D": (0, -1, 0),
    "L": (-1, 0, 0),
    "B": (0, 0, -1),
}

Vec = Tuple[int, int, int]


def _facelet_coords() -> List[Tuple[Vec, Vec]]:
    """Return (cubie position, sticker direction) for each of the 54 facelets."""
    coords: List[Tuple[Vec, Vec]] = [((0, 0, 0), (0, 0, 0))] * 54
    # x = right, y = up, z = towards the viewer (front)
    builders: List[Callable[[int, int], Tuple[Vec, Vec]]] = [
        lambda r, c: ((c - 1, 1, r - 1), (0, 1, 0)),  # U
        lambda r, c: ((1, 1 - r, 1 - c), (1, 0, 0)),  # R
        lambda r, c: ((c - 1, 1 - r, 1), (0, 0, 1)),  # F
        lambda r, c: ((c - 1, -1, 1 - r), (0, -1, 0)),  # D
        lambda r, c: ((-1, 1 - r, c - 1), (-1, 0, 0)),  # L
        lambda r, c: ((1 - c, 1 - r, -1), (0, 0, -1)),  # B
    ]
    for face_index, build in enumerate(builders):
        for i in range(9):
            r, c = divmod(i, 3)
            coords[face_index * 9 + i] = build(r, c)
    return coords


def _cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vec, b: Vec) -> int:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _rotate_clockwise(v: Vec, axis: Vec) -> Vec:
    """Rotate v by -90 degrees around ``axis`` (= clockwise seen from outside)."""
    c = _cross(axis, v)
    d = _dot(axis, v)
    return (-c[0] + axis[0] * d, -c[1] + axis[1] * d, -c[2] + axis[2] * d)


def _build_quarter_turn_sources() -> Dict[str, List[int]]:
    """For each face, list ``src`` so that ``new[k] = old[src[k]]`` after a clockwise turn."""
    coords = _facelet_coords()
    index_of = {coords[i]: i for i in range(54)}
    sources: Dict[str, List[int]] = {}
    for face, normal in _FACE_NORMAL.items():
        src = list(range(54))
        for j, (pos, direction) in enumerate(coords):
            if _dot(pos, normal) != 1:  # sticker is not in the turning layer
                continue
            dest = index_of[(_rotate_clockwise(pos, normal), _rotate_clockwise(direction, normal))]
            src[dest] = j
        sources[face] = src
    return sources


def _compose(first: Sequence[int], second: Sequence[int]) -> List[int]:
    """Apply ``first`` and then ``second`` (both in ``src`` form)."""
    return [first[second[k]] for k in range(54)]


def _build_getters() -> Dict[str, Callable[[str], Tuple[str, ...]]]:
    quarter = _build_quarter_turn_sources()
    getters: Dict[str, Callable[[str], Tuple[str, ...]]] = {}
    for face in FACE_LETTERS:
        q1 = quarter[face]
        q2 = _compose(q1, q1)
        q3 = _compose(q2, q1)
        getters[face] = itemgetter(*q1)
        getters[face + "2"] = itemgetter(*q2)
        getters[face + "'"] = itemgetter(*q3)
    return getters


_GETTERS = _build_getters()


def is_valid_move(move: object) -> bool:
    return isinstance(move, str) and move in _GETTERS


def apply_move_str(state: str, move: str) -> str:
    """Apply one move to a 54-character state string (fast path used by the solver)."""
    return "".join(_GETTERS[move](state))


def invert_move(move: str) -> str:
    if move.endswith("2"):
        return move
    if move.endswith("'"):
        return move[0]
    return move + "'"


def invert_sequence(moves: Iterable[str]) -> List[str]:
    return [invert_move(m) for m in reversed(list(moves))]


def parse_sequence(text: str) -> List[str]:
    """'R U R'' -> ['R', 'U', "R'"]. Raises ValueError on an unknown token."""
    moves = text.replace(",", " ").split()
    for m in moves:
        if not is_valid_move(m):
            raise ValueError(f"unknown move: {m!r}")
    return moves


def stickers_in_place(state: str) -> int:
    """How many of the 54 stickers match the face they sit on (54 = solved)."""
    return sum(1 for i, ch in enumerate(state) if ch == FACES[i // 9])


@dataclass(frozen=True)
class Cube:
    """Immutable cube state. ``Cube.apply`` returns a new cube."""

    facelets: str = SOLVED

    def __post_init__(self) -> None:
        validate_facelets(self.facelets)

    @classmethod
    def solved(cls) -> "Cube":
        return cls(SOLVED)

    @classmethod
    def from_moves(cls, moves: Iterable[str]) -> "Cube":
        return cls(SOLVED).apply_sequence(moves)

    def apply(self, move: str) -> "Cube":
        if not is_valid_move(move):
            raise ValueError(f"invalid move: {move!r}")
        return Cube(apply_move_str(self.facelets, move))

    def apply_sequence(self, moves: Iterable[str]) -> "Cube":
        state = self.facelets
        for m in moves:
            if not is_valid_move(m):
                raise ValueError(f"invalid move: {m!r}")
            state = apply_move_str(state, m)
        return Cube(state)

    def is_solved(self) -> bool:
        """True when all six faces show one single colour."""
        s = self.facelets
        return all(s[i * 9 : i * 9 + 9] == s[i * 9] * 9 for i in range(6))

    def stickers_in_place(self) -> int:
        return stickers_in_place(self.facelets)

    def faces(self) -> Dict[str, str]:
        return {f: self.facelets[i * 9 : i * 9 + 9] for i, f in enumerate(FACES)}

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.facelets


def validate_facelets(facelets: str) -> None:
    if not isinstance(facelets, str) or len(facelets) != 54:
        raise ValueError("facelets must be a string with 54 characters")
    for letter in FACES:
        if facelets.count(letter) != 9:
            raise ValueError(f"facelets must contain exactly 9 x {letter!r}")
    for i, face in enumerate(FACES):
        if facelets[i * 9 + 4] != face:
            raise ValueError(f"centre of face {face} must be {face!r}")
