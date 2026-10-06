"""Turn-based game rules.

Pure functions only: no Flask, no database, no imports from the app. Every
function takes a state and returns a new one, never mutating the argument -
the Game.state column is db.JSON, which does not notice in-place edits.

Each entry in GAMES provides:
    new_state()                  -> state
    apply_move(state, seat, move) -> (state, error)   error is None on success
    result(state)                -> None | ("win", seat) | ("draw", None)
    view(state, seat)            -> what that seat is allowed to see
"""

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


# -------------------------------------------------------------------- registry

def _open_view(state, seat):
    """Nothing is hidden in these games, so both seats see the same board."""
    return state


GAMES = {
    "tictactoe": {
        "new_state": _ttt_new,
        "apply_move": _ttt_move,
        "result": _ttt_result,
        "view": _open_view,
    },
    "connect4": {
        "new_state": _c4_new,
        "apply_move": _c4_move,
        "result": _c4_result,
        "view": _open_view,
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
