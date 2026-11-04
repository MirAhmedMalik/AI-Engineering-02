import random

import pytest

from app.cube import (
    ALL_MOVES,
    SOLVED,
    Cube,
    OptimalSolver,
    apply_move_str,
    invert_move,
    invert_sequence,
    is_valid_move,
    parse_sequence,
    stickers_in_place,
)
from app.cube.puzzles import TEST_SETS, make_puzzle, make_puzzles, random_scramble


def test_there_are_18_moves():
    assert len(ALL_MOVES) == 18
    assert len(set(ALL_MOVES)) == 18
    assert all(is_valid_move(m) for m in ALL_MOVES)
    assert not is_valid_move("X")
    assert not is_valid_move("r")
    assert not is_valid_move(None)


def test_solved_state():
    assert len(SOLVED) == 54
    assert Cube.solved().is_solved()
    assert stickers_in_place(SOLVED) == 54


@pytest.mark.parametrize("move", ALL_MOVES)
def test_every_move_has_correct_order(move):
    cube = Cube.solved().apply(move)
    assert not cube.is_solved()
    order = 2 if move.endswith("2") else 4
    state = SOLVED
    for _ in range(order):
        state = apply_move_str(state, move)
    assert state == SOLVED


@pytest.mark.parametrize("move", ALL_MOVES)
def test_inverse_cancels(move):
    s = Cube.solved().apply("R").apply("U").apply(move).apply(invert_move(move))
    assert s.facelets == Cube.from_moves(["R", "U"]).facelets


def test_sexy_move_has_order_six():
    state = SOLVED
    for i in range(6):
        for m in ["R", "U", "R'", "U'"]:
            state = apply_move_str(state, m)
        if i < 5:
            assert state != SOLVED
    assert state == SOLVED


def test_known_geometry_u_moves_right_row_to_front():
    # After U (clockwise seen from the top) the top row of F shows what was on R.
    cube = Cube.solved().apply("U")
    faces = cube.faces()
    assert faces["F"][:3] == "RRR"
    assert faces["L"][:3] == "FFF"
    assert faces["B"][:3] == "LLL"
    assert faces["R"][:3] == "BBB"
    assert faces["U"] == "U" * 9


def test_known_geometry_r_moves_front_column_up():
    # R clockwise: right column of F goes up to U
    faces = Cube.solved().apply("R").faces()
    assert faces["U"][2] == "F" and faces["U"][5] == "F" and faces["U"][8] == "F"
    assert faces["B"][0] == "U"  # top of U goes to B (mirrored)
    assert faces["R"] == "R" * 9


def test_centres_never_move():
    state = SOLVED
    rng = random.Random(1)
    for _ in range(200):
        state = apply_move_str(state, rng.choice(ALL_MOVES))
    assert [state[i * 9 + 4] for i in range(6)] == list("URFDLB")
    for letter in "URFDLB":
        assert state.count(letter) == 9


def test_invert_sequence_round_trip():
    rng = random.Random(7)
    seq = [rng.choice(ALL_MOVES) for _ in range(25)]
    cube = Cube.from_moves(seq).apply_sequence(invert_sequence(seq))
    assert cube.is_solved()


def test_parse_sequence():
    assert parse_sequence("R U R' U2") == ["R", "U", "R'", "U2"]
    with pytest.raises(ValueError):
        parse_sequence("R Q")


def test_invalid_facelets_rejected():
    with pytest.raises(ValueError):
        Cube("U" * 53)
    with pytest.raises(ValueError):
        Cube("U" * 54)


def test_apply_rejects_unknown_move():
    with pytest.raises(ValueError):
        Cube.solved().apply("R3")


# ---------------------------------------------------------------- solver
@pytest.fixture(scope="module")
def solver():
    return OptimalSolver()


def test_table_size(solver):
    # cumulative number of positions at depth 0..4 in HTM: 1+18+243+3240+43239
    assert len(solver.table) == 1 + 18 + 243 + 3240 + 43239


def test_solver_solves_and_is_optimal_for_known_cases(solver):
    assert solver.solve(SOLVED) == []
    assert solver.distance(Cube.from_moves(["R"]).facelets) == 1
    assert solver.distance(Cube.from_moves(["R", "U"]).facelets) == 2
    assert solver.distance(Cube.from_moves(["R2"]).facelets) == 1
    # a commuting pair "U D" still needs 2 moves
    assert solver.distance(Cube.from_moves(["U", "D"]).facelets) == 2


def test_solver_solution_actually_solves(solver):
    rng = random.Random(123)
    for n in range(1, 9):
        scramble = random_scramble(n, rng)
        cube = Cube.from_moves(scramble)
        solution = solver.solve(cube.facelets)
        assert solution is not None
        assert len(solution) <= n  # optimum is never longer than the scramble
        assert cube.apply_sequence(solution).is_solved()


def test_solver_distance_matches_independent_bfs(solver):
    """Cross-check: plain forward BFS (no table) agrees with meet-in-the-middle."""
    rng = random.Random(99)
    for _ in range(15):
        n = rng.randint(1, 5)
        cube = Cube.from_moves(random_scramble(n, rng))
        # independent plain BFS
        frontier = {cube.facelets}
        seen = set(frontier)
        dist = None
        for depth in range(0, 6):
            if SOLVED in frontier:
                dist = depth
                break
            nxt = set()
            for s in frontier:
                for m in ALL_MOVES:
                    t = apply_move_str(s, m)
                    if t not in seen:
                        seen.add(t)
                        nxt.add(t)
            frontier = nxt
        assert dist == solver.distance(cube.facelets)


def test_solver_returns_none_when_too_deep(solver):
    rng = random.Random(5)
    cube = Cube.from_moves(random_scramble(20, rng))
    assert solver.solve(cube.facelets) is None


# ---------------------------------------------------------------- puzzles
def test_puzzles_are_deterministic_and_valid(solver):
    a = make_puzzles("primary", 5, 42, solver)
    b = make_puzzles("primary", 5, 42, solver)
    assert [p.facelets for p in a] == [p.facelets for p in b]
    for p in a:
        spec = TEST_SETS["primary"]
        assert spec.min_turns <= p.scramble_len <= spec.max_turns
        cube = Cube.from_moves(p.scramble)
        assert cube.facelets == p.facelets
        assert not cube.is_solved()
        assert p.reference_kind == "exact"
        assert 1 <= p.reference_moves <= p.scramble_len
        assert cube.apply_sequence(p.reference_solution).is_solved()
        assert len(p.reference_solution) == p.reference_moves


def test_extension_puzzles_use_upper_bound(solver):
    p = make_puzzle("extension", 1, 0, solver)
    assert 15 <= p.scramble_len <= 20
    assert p.reference_kind == "upper_bound"
    assert p.reference_moves <= p.scramble_len
    assert Cube(p.facelets).apply_sequence(p.reference_solution).is_solved()


def test_different_seeds_give_different_puzzles(solver):
    a = make_puzzles("primary", 5, 1, solver)
    b = make_puzzles("primary", 5, 2, solver)
    assert [p.facelets for p in a] != [p.facelets for p in b]
