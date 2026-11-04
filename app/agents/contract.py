"""The game-action contract shared by ALL models.

Both interfaces (Decisions API and chat completions) are built from the same
pieces in this file, so every model gets exactly the same information:

  * the same state (54 facelets + recent moves + move counter)
  * the same 18 allowed moves with the same descriptions
  * the same explanation of the cube layout

Only the wrapper changes: a typed ``choice`` question for the decision models,
and a system + user message with a JSON schema for the chat model.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

from ..cube.engine import ALL_MOVES, FACES, SOLVED

FACE_NAMES = {"U": "Up", "R": "Right", "F": "Front", "D": "Down", "L": "Left", "B": "Back"}


def move_description(move: str) -> str:
    face = FACE_NAMES[move[0]]
    if move.endswith("2"):
        return f"Turn the {face} face by 180 degrees"
    if move.endswith("'"):
        return f"Turn the {face} face counterclockwise by 90 degrees (seen from outside that face)"
    return f"Turn the {face} face clockwise by 90 degrees (seen from outside that face)"


MOVE_DESCRIPTIONS: Dict[str, str] = {m: move_description(m) for m in ALL_MOVES}

FACELET_LAYOUT = (
    "cube_facelets is a string of 54 letters: the faces in the order U, R, F, D, L, B with 9 letters each. "
    "Each face is read row by row (left to right, top to bottom) as if you look straight at that face from outside. "
    "For U the top row is next to B and the bottom row is next to F. For D the top row is next to F and the bottom row is next to B. "
    "For R, F, L and B the top row is next to U. "
    "Every letter names the face where that sticker belongs. "
    f"The cube is solved when every face has only one letter: {SOLVED}. "
    "A move that is only a letter turns that face 90 degrees clockwise (seen from outside that face); a letter with ' is 90 degrees counterclockwise; a letter with 2 is 180 degrees. "
    "Example: U moves the top row of R onto F, the top row of F onto L, the top row of L onto B, and the top row of B onto R."
)


GAME_HINT = (
    "The cube starts only a few moves away from solved, so a short solution exists. "
    "Do not play a move and then its inverse right away, and do not repeat a position you have already seen."
)


PROMPT_STYLES = ("standard", "compact", "plain")


def prompt_style() -> str:
    """Wording of the question. The SAME style is used for every model of a run (set PROMPT_STYLE in .env).

    standard: full state (facelets, faces, recent moves, counters) and the long instructions.
    compact:  only cube_facelets + recent_moves in the state, shorter instructions.
    plain:    like compact, and every move is described in a few words.
    """
    style = os.environ.get("PROMPT_STYLE", "standard").strip().lower() or "standard"
    return style if style in PROMPT_STYLES else "standard"


def build_state_payload(facelets: str, recent_moves: List[str], moves_made: int, move_limit: int) -> dict:
    """The state object. Identical for every model."""
    if prompt_style() != "standard":
        return {"cube_facelets": facelets, "recent_moves": list(recent_moves)}
    return {
        "cube_facelets": facelets,
        "faces": {f: facelets[i * 9 : i * 9 + 9] for i, f in enumerate(FACES)},
        "recent_moves": list(recent_moves),
        "moves_made": moves_made,
        "moves_remaining": move_limit - moves_made,
    }


DECISION_INSTRUCTIONS = (
    "You are playing a 3x3 Rubik's Cube. Choose the single next move most likely to progress toward the solved cube. "
    + FACELET_LAYOUT
    + " " + GAME_HINT
    + " Pick exactly one move from the options."
)


SHORT_INSTRUCTIONS = (
    "Which single move brings this 3x3 Rubik's Cube closest to solved? "
    + FACELET_LAYOUT
)


def short_description(move: str) -> str:
    face = FACE_NAMES[move[0]]
    if move.endswith("2"):
        return f"{face} face, half turn"
    if move.endswith("'"):
        return f"{face} face, quarter turn counterclockwise"
    return f"{face} face, quarter turn clockwise"


def build_decision_question(allowed_moves: List[str]) -> dict:
    """One typed 'choice' question. Options = the allowed moves."""
    style = prompt_style()
    if style == "standard":
        instructions, criteria = DECISION_INSTRUCTIONS, {m: MOVE_DESCRIPTIONS[m] for m in allowed_moves}
    elif style == "compact":
        instructions, criteria = SHORT_INSTRUCTIONS, {m: MOVE_DESCRIPTIONS[m] for m in allowed_moves}
    else:
        instructions, criteria = SHORT_INSTRUCTIONS, {m: short_description(m) for m in allowed_moves}
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


LLM_SYSTEM_PROMPT = (
    "You are an expert 3x3 Rubik's Cube solver playing a turn-based game. "
    "In every turn you see the current cube and you must choose exactly ONE next move. "
    "The game applies your move and then asks you again, until the cube is solved or the move limit is reached. "
    "Fewer moves is better. "
    + FACELET_LAYOUT
    + " " + GAME_HINT
    + " Answer with a JSON object with one key, \"move\", whose value is one of the allowed moves. "
    "Do not add any other text."
)

MOVE_JSON_SCHEMA = {
    "type": "object",
    "properties": {"move": {"type": "string", "enum": list(ALL_MOVES)}},
    "required": ["move"],
    "additionalProperties": False,
}


def build_llm_user_prompt(state_payload: dict, allowed_moves: List[str]) -> str:
    return (
        "Current state:\n"
        + json.dumps(state_payload, indent=2)
        + "\n\nAllowed moves:\n"
        + json.dumps(allowed_moves)
        + "\n\nChoose the single next move most likely to progress toward the solved cube. "
        'Reply only with JSON like {"move": "R\'"}.'
    )
