# portfolio/position_manager.py
# Python 3.8-safe

import time

POSITIONS = {}

# ── STOP LOSS ─────────────────────────────────────────────────────────────────
# SL inicial: -20%. Se mueve con cada TP:
#   TP1 hit → SL sube a break-even (entry × 1.0)
#   TP2 hit → SL sube a +25%       (entry × 1.25)
#   TP3 hit → SL sube a +50%       (entry × 1.50)
#   TP4 hit → moonbag permanente, sin SL
STOP_LOSS_INITIAL = 0.20   # -20%

# ── TAKE PROFIT LEVELS ────────────────────────────────────────────────────────
TP1_PCT = 0.25   # +25%  → vende 30% del initial_amount_raw
TP2_PCT = 0.50   # +50%  → vende 20%
TP3_PCT = 1.00   # +100% → vende 35%
TP4_PCT = 2.00   # +200% → vende 10%
# Moonbag residual: 5% permanente (30+20+35+10 = 95%)

TP1_SELL_FRAC = 0.30
TP2_SELL_FRAC = 0.20
TP3_SELL_FRAC = 0.35
TP4_SELL_FRAC = 0.10

# ── TIMEOUT ───────────────────────────────────────────────────────────────────
MAX_HOLD_SEC         = 3600   # 1 hora
INACTIVITY_EXIT_SEC  = 120    # 2 min sin trades → salida


def add_position(token_mint, entry_price=None, amount_raw=None, symbol=""):
    if token_mint in POSITIONS:
        return

    POSITIONS[token_mint] = {
        "token_mint":         token_mint,
        "symbol":             symbol,
        "entry_price":        entry_price,
        "amount_raw":         amount_raw,
        "initial_amount_raw": amount_raw,
        "created_at":         int(time.time()),
        "closed":             False,
        "tp1_done":           False,
        "tp2_done":           False,
        "tp3_done":           False,
        "tp4_done":           False,
        "moonbag_left":       False,
        # SL dinámico: fracción mínima de entry_price permitida
        # 0.80 = SL en -20%  |  1.00 = break-even  |  1.25 = +25%
        "sl_floor_mult":      1.0 - STOP_LOSS_INITIAL,  # 0.80
        "last_trade_ts":      int(time.time()),
    }

    print("[POSITION ADDED] token={} entry_price={} amount_raw={}".format(
        token_mint, entry_price, amount_raw
    ))


def update_position_after_buy(token_mint, entry_price=None, amount_raw=None):
    pos = POSITIONS.get(token_mint)
    if not pos:
        add_position(token_mint, entry_price=entry_price, amount_raw=amount_raw)
        return
    if entry_price is not None:
        pos["entry_price"] = entry_price
    if amount_raw is not None:
        pos["amount_raw"] = amount_raw
        if pos.get("initial_amount_raw") is None:
            pos["initial_amount_raw"] = amount_raw


def list_open_positions():
    return [pos for pos in POSITIONS.values() if not pos.get("closed")]


def mark_closed(token_mint):
    pos = POSITIONS.get(token_mint)
    if pos:
        pos["closed"] = True


def mark_tp1_done(token_mint):
    pos = POSITIONS.get(token_mint)
    if pos:
        pos["tp1_done"] = True
        pos["sl_floor_mult"] = 1.0          # SL → break-even


def mark_tp2_done(token_mint):
    pos = POSITIONS.get(token_mint)
    if pos:
        pos["tp2_done"] = True
        pos["sl_floor_mult"] = 1.0 + TP1_PCT   # SL → +25%


def mark_tp3_done(token_mint):
    pos = POSITIONS.get(token_mint)
    if pos:
        pos["tp3_done"] = True
        pos["sl_floor_mult"] = 1.0 + TP2_PCT   # SL → +50%


def mark_tp4_done(token_mint):
    pos = POSITIONS.get(token_mint)
    if pos:
        pos["tp4_done"] = True
        pos["moonbag_left"] = True              # sin SL posterior


def mark_moonbag_left(token_mint):
    pos = POSITIONS.get(token_mint)
    if pos:
        pos["moonbag_left"] = True


def update_last_trade_ts(token_mint, trade_ts=None):
    pos = POSITIONS.get(token_mint)
    if pos:
        pos["last_trade_ts"] = int(trade_ts or time.time())


# ── INACTIVIDAD ───────────────────────────────────────────────────────────────
def should_inactivity_exit(pos):
    """
    Si no hubo trades en INACTIVITY_EXIT_SEC segundos → salida.
    El moonbag (TP4 alcanzado) queda exento.
    """
    if pos.get("tp4_done"):
        return False
    last_trade_ts = int(pos.get("last_trade_ts") or pos.get("created_at") or int(time.time()))
    idle_sec = int(time.time()) - last_trade_ts
    return idle_sec >= INACTIVITY_EXIT_SEC


# ── STOP LOSS DINÁMICO ────────────────────────────────────────────────────────
def should_stop_loss(pos, pnl_pct):
    """
    El moonbag (TP4 alcanzado) no tiene SL.
    Para el resto: SL se dispara si current < entry * sl_floor_mult.
    pnl_pct = (current/entry) - 1  →  current/entry = 1 + pnl_pct
    """
    if pos.get("tp4_done"):
        return False
    floor = pos.get("sl_floor_mult", 1.0 - STOP_LOSS_INITIAL)
    return (1.0 + pnl_pct) < floor


# ── TAKE PROFIT CHECKS ────────────────────────────────────────────────────────
def should_take_profit_1(pos, pnl_pct):
    return (not pos.get("tp1_done")) and pnl_pct >= TP1_PCT


def should_take_profit_2(pos, pnl_pct):
    return pos.get("tp1_done") and (not pos.get("tp2_done")) and pnl_pct >= TP2_PCT


def should_take_profit_3(pos, pnl_pct):
    return pos.get("tp2_done") and (not pos.get("tp3_done")) and pnl_pct >= TP3_PCT


def should_take_profit_4(pos, pnl_pct):
    return pos.get("tp3_done") and (not pos.get("tp4_done")) and pnl_pct >= TP4_PCT


# ── TIMEOUT ───────────────────────────────────────────────────────────────────
def should_timeout_full_exit(pos):
    """Sin TP1 alcanzado después de MAX_HOLD_SEC → salida total."""
    age = int(time.time()) - pos["created_at"]
    return (not pos.get("tp1_done")) and age >= MAX_HOLD_SEC


def should_timeout_keep_moonbag(pos):
    """Con TP1 alcanzado y timeout → moonbag se queda indefinidamente."""
    age = int(time.time()) - pos["created_at"]
    return pos.get("tp1_done") and age >= MAX_HOLD_SEC