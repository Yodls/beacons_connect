"""Turn-based game rules.

Pure functions only: no Flask, no database, no imports from the app. Every
function takes a state and returns a new one, never mutating the argument -
the Game.state column is db.JSON, which does not notice in-place edits.

Each entry in GAMES provides:
    new_state()                   -> state
    apply_move(state, seat, move) -> (state, error)   error is None on success
    result(state)                 -> None | ("win", seat) | ("draw", None)
    view(state, seat)             -> what that seat is allowed to see
    can_move(state, seat)         -> bool
    turn_seat(state)              -> seat whose turn it is, or None

can_move exists because Battleship's placement phase lets both players act at
once, which a single `turn` field cannot express.
"""
import random

import chess

KINDS = ("tictactoe", "connect4", "battleship", "chess")

LABELS = {
    "tictactoe": "Tic-Tac-Toe",
    "connect4": "Connect Four",
    "battleship": "Battleship",
    "chess": "Chess",
}


# ---------------------------------------------------------------- tic-tac-toe

_TTT_LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)


def _ttt_new():
    return {"board": [None] * 9, "turn": 0}


def _ttt_move(state, seat, move):
    if state["turn"] != seat:
        return state, "It isn't your turn."
    try:
        cell = int(move)
    except (TypeError, ValueError):
        return state, "That isn't a square."
    if not 0 <= cell <= 8:
        return state, "That isn't a square."
    if state["board"][cell] is not None:
        return state, "That square is taken."

    board = list(state["board"])
    board[cell] = seat
    return {"board": board, "turn": 1 - seat}, None


def _ttt_result(state):
    board = state["board"]
    for a, b, c in _TTT_LINES:
        if board[a] is not None and board[a] == board[b] == board[c]:
            return ("win", board[a])
    if all(cell is not None for cell in board):
        return ("draw", None)
    return None


# --------------------------------------------------------------- connect four

_C4_ROWS = 6
_C4_COLS = 7


def _c4_new():
    return {
        "board": [[None] * _C4_COLS for _ in range(_C4_ROWS)],
        "turn": 0,
    }


def _c4_move(state, seat, move):
    if state["turn"] != seat:
        return state, "It isn't your turn."
    try:
        col = int(move)
    except (TypeError, ValueError):
        return state, "That isn't a column."
    if not 0 <= col < _C4_COLS:
        return state, "That isn't a column."

    board = [list(row) for row in state["board"]]
    for row in range(_C4_ROWS - 1, -1, -1):          # gravity: lowest empty row
        if board[row][col] is None:
            board[row][col] = seat
            return {"board": board, "turn": 1 - seat}, None

    return state, "That column is full."


def _c4_result(state):
    board = state["board"]
    for row in range(_C4_ROWS):
        for col in range(_C4_COLS):
            seat = board[row][col]
            if seat is None:
                continue
            for drow, dcol in ((0, 1), (1, 0), (1, 1), (1, -1)):
                cells = []
                for step in range(4):
                    r, c = row + drow * step, col + dcol * step
                    if 0 <= r < _C4_ROWS and 0 <= c < _C4_COLS:
                        cells.append(board[r][c])
                if len(cells) == 4 and all(x == seat for x in cells):
                    return ("win", seat)

    if all(cell is not None for row in board for cell in row):
        return ("draw", None)
    return None


# ----------------------------------------------------------------- battleship

BS_SIZE = 10
BS_FLEET = (
    ("Carrier", 5),
    ("Battleship", 4),
    ("Cruiser", 3),
    ("Submarine", 3),
    ("Destroyer", 2),
)
_BS_CELLS = BS_SIZE * BS_SIZE


def _bs_layout(rng=None):
    """A random legal fleet: no overlaps, nothing off the edge."""
    rng = rng or random
    ships = []
    taken = set()
    for name, length in BS_FLEET:
        while True:
            horizontal = rng.random() < 0.5
            if horizontal:
                row = rng.randrange(BS_SIZE)
                col = rng.randrange(BS_SIZE - length + 1)
                cells = [row * BS_SIZE + col + i for i in range(length)]
            else:
                row = rng.randrange(BS_SIZE - length + 1)
                col = rng.randrange(BS_SIZE)
                cells = [(row + i) * BS_SIZE + col for i in range(length)]
            if not taken.intersection(cells):
                taken.update(cells)
                ships.append({"name": name, "cells": cells})
                break
    return ships


def _bs_new():
    return {
        "phase": "placing",
        "turn": 0,
        "ready": [False, False],
        "ships": [_bs_layout(), _bs_layout()],
        "shots": [[], []],
    }


def _bs_copy(state):
    return {
        "phase": state["phase"],
        "turn": state["turn"],
        "ready": list(state["ready"]),
        "ships": [[dict(s, cells=list(s["cells"])) for s in side]
                  for side in state["ships"]],
        "shots": [list(side) for side in state["shots"]],
    }


def _bs_move(state, seat, move):
    if state["phase"] == "placing":
        if state["ready"][seat]:
            return state, "Your fleet is already set."
        if move == "shuffle":
            new = _bs_copy(state)
            new["ships"][seat] = _bs_layout()
            return new, None
        if move == "ready":
            new = _bs_copy(state)
            new["ready"][seat] = True
            if all(new["ready"]):
                new["phase"] = "firing"
                new["turn"] = 0
            return new, None
        return state, "Shuffle your fleet or mark it ready."

    if state["turn"] != seat:
        return state, "It isn't your turn."
    try:
        cell = int(move)
    except (TypeError, ValueError):
        return state, "That isn't a square."
    if not 0 <= cell < _BS_CELLS:
        return state, "That isn't a square."
    if cell in state["shots"][seat]:
        return state, "You already fired there."

    new = _bs_copy(state)
    new["shots"][seat].append(cell)
    new["turn"] = 1 - seat
    return new, None


def _bs_sunk(ships, shots):
    return [s for s in ships if set(s["cells"]).issubset(shots)]


def _bs_result(state):
    if state["phase"] != "firing":
        return None
    for seat in (0, 1):
        enemy_cells = {c for s in state["ships"][1 - seat] for c in s["cells"]}
        if enemy_cells and enemy_cells.issubset(set(state["shots"][seat])):
            return ("win", seat)
    return None


def _bs_view(state, seat):
    """Your fleet in full; theirs only where you have already fired."""
    mine = state["ships"][seat]
    their_shots = set(state["shots"][1 - seat])
    my_shots = set(state["shots"][seat])
    their_cells = {c for s in state["ships"][1 - seat] for c in s["cells"]}

    my_ship_cells = {c for s in mine for c in s["cells"]}
    my_grid = [
        {
            "ship": cell in my_ship_cells,
            "hit": cell in their_shots and cell in my_ship_cells,
            "miss": cell in their_shots and cell not in my_ship_cells,
        }
        for cell in range(_BS_CELLS)
    ]
    their_grid = [
        {
            "shot": cell in my_shots,
            "hit": cell in my_shots and cell in their_cells,
            "miss": cell in my_shots and cell not in their_cells,
        }
        for cell in range(_BS_CELLS)
    ]

    return {
        "phase": state["phase"],
        "size": BS_SIZE,
        "ready": list(state["ready"]),
        "you_ready": state["ready"][seat],
        "my_grid": my_grid,
        "their_grid": their_grid,
        "my_fleet": [
            {"name": s["name"], "length": len(s["cells"]),
             "sunk": set(s["cells"]).issubset(their_shots)}
            for s in mine
        ],
        "their_sunk": [s["name"] for s in _bs_sunk(state["ships"][1 - seat], my_shots)],
        "turn": state["turn"],
    }


def _bs_can_move(state, seat):
    if state["phase"] == "placing":
        return not state["ready"][seat]
    return state["turn"] == seat


def _bs_turn_seat(state):
    return None if state["phase"] == "placing" else state["turn"]


# ----------------------------------------------------------------------- chess

_PIECE_GLYPHS = {
    "P": "♙", "N": "♘", "B": "♗",
    "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝",
    "r": "♜", "q": "♛", "k": "♚",
}


def _chess_new():
    return {"fen": chess.STARTING_FEN, "turn": 0, "last": None}


def _chess_seat_to_move(board):
    return 0 if board.turn == chess.WHITE else 1


def _chess_parse(board, move):
    """Accept UCI, UCI needing a promotion suffix, or SAN."""
    if not isinstance(move, str):
        return None
    text = move.strip()
    if not text:
        return None

    try:
        parsed = chess.Move.from_uci(text)
        if parsed in board.legal_moves:
            return parsed
        # A pawn reaching the last rank needs a promotion piece; assume a queen.
        promoted = chess.Move(parsed.from_square, parsed.to_square,
                              promotion=chess.QUEEN)
        if promoted in board.legal_moves:
            return promoted
    except ValueError:
        pass

    try:
        return board.parse_san(text)
    except ValueError:
        return None


def _chess_move(state, seat, move):
    board = chess.Board(state["fen"])
    if _chess_seat_to_move(board) != seat:
        return state, "It isn't your turn."

    parsed = _chess_parse(board, move)
    if parsed is None:
        return state, "That isn't a legal move."

    board.push(parsed)
    return {
        "fen": board.fen(),
        "turn": _chess_seat_to_move(board),
        "last": parsed.uci(),
    }, None


def _chess_result(state):
    board = chess.Board(state["fen"])
    if board.is_checkmate():
        # The side to move is mated, so the other seat won.
        return ("win", 1 - _chess_seat_to_move(board))
    if (board.is_stalemate()
            or board.is_insufficient_material()
            or board.is_seventyfive_moves()
            or board.is_fivefold_repetition()):
        return ("draw", None)
    return None


def _chess_view(state, seat):
    board = chess.Board(state["fen"])
    to_move = _chess_seat_to_move(board)

    # Rank 8 first for white; flipped so black sees their own pieces nearest.
    ranks = range(7, -1, -1) if seat == 0 else range(8)
    files = range(8) if seat == 0 else range(7, -1, -1)

    rows = []
    for rank in ranks:
        row = []
        for file in files:
            square = chess.square(file, rank)
            piece = board.piece_at(square)
            row.append({
                "name": chess.square_name(square),
                "glyph": _PIECE_GLYPHS.get(piece.symbol(), "") if piece else "",
                "mine": piece is not None
                        and (piece.color == chess.WHITE) == (seat == 0),
                "dark": (file + rank) % 2 == 0,
            })
        rows.append(row)

    return {
        "rows": rows,
        "fen": state["fen"],
        "last": state.get("last"),
        "check": board.is_check(),
        "you_are": "White" if seat == 0 else "Black",
        "legal": sorted(m.uci() for m in board.legal_moves) if to_move == seat else [],
        "turn": to_move,
    }


def _chess_turn_seat(state):
    return _chess_seat_to_move(chess.Board(state["fen"]))


# -------------------------------------------------------------------- registry

def _open_view(state, seat):
    """Nothing is hidden in these games, so both seats see the same board."""
    return state


def _turn_can_move(state, seat):
    return state["turn"] == seat


def _turn_seat(state):
    return state["turn"]


GAMES = {
    "tictactoe": {
        "new_state": _ttt_new,
        "apply_move": _ttt_move,
        "result": _ttt_result,
        "view": _open_view,
        "can_move": _turn_can_move,
        "turn_seat": _turn_seat,
    },
    "connect4": {
        "new_state": _c4_new,
        "apply_move": _c4_move,
        "result": _c4_result,
        "view": _open_view,
        "can_move": _turn_can_move,
        "turn_seat": _turn_seat,
    },
    "battleship": {
        "new_state": _bs_new,
        "apply_move": _bs_move,
        "result": _bs_result,
        "view": _bs_view,
        "can_move": _bs_can_move,
        "turn_seat": _bs_turn_seat,
    },
    "chess": {
        "new_state": _chess_new,
        "apply_move": _chess_move,
        "result": _chess_result,
        "view": _chess_view,
        "can_move": lambda state, seat: _chess_turn_seat(state) == seat,
        "turn_seat": _chess_turn_seat,
    },
}

PLAYABLE = tuple(GAMES)


def new_state(kind):
    return GAMES[kind]["new_state"]()


def apply_move(kind, state, seat, move):
    return GAMES[kind]["apply_move"](state, seat, move)


def result(kind, state):
    return GAMES[kind]["result"](state)


def view(kind, state, seat):
    return GAMES[kind]["view"](state, seat)


def can_move(kind, state, seat):
    return GAMES[kind]["can_move"](state, seat)


def turn_seat(kind, state):
    return GAMES[kind]["turn_seat"](state)
