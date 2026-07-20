# analytics/logger.py
# Python 3.8-safe

import sqlite3
import time

DB_PATH = "analytics.db"


def _connect():
    return sqlite3.connect(DB_PATH)


def _get_columns(conn, table_name):
    """Return the set of column names for table_name, or empty set if it doesn't exist."""
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    if cur.fetchone() is None:
        return set()
    cur.execute("PRAGMA table_info({})".format(table_name))
    return {row[1] for row in cur.fetchall()}


def _ensure_events_schema(conn):
    required = {
        # ── original columns ──────────────────────────────────────────────────
        "timestamp":        "INTEGER",
        "address":          "TEXT",
        "symbol":           "TEXT",
        "action":           "TEXT",
        "reason":           "TEXT",
        "checkpoint":       "TEXT",
        "trades":           "INTEGER",
        "volume":           "REAL",
        "buys":             "INTEGER",
        "sells":            "INTEGER",
        "buy_volume":       "REAL",
        "sell_volume":      "REAL",
        "pair_age":         "INTEGER",
        "market_cap":       "REAL",
        "liquidity":        "REAL",
        "trade_speed":      "REAL",
        "recent_sells_pct": "REAL",
        "score":            "REAL",
        "detail":           "TEXT",
        # ── momentum / CP5 additions ──────────────────────────────────────────
        "buy_volume_pct":   "REAL",   # fraction of volume on buy side [0–1]
        "acceleration":     "REAL",   # fast_speed / slow_speed ratio
        "vol_15":           "REAL",   # SOL volume in last 15 s window
        "vol_60":           "REAL",   # SOL volume in last 60 s window
        "speed_15":         "REAL",   # trades/s in last 15 s window
        "speed_60":         "REAL",   # trades/s in last 60 s window
    }

    existing = _get_columns(conn, "events")
    cur = conn.cursor()
    for col, col_type in required.items():
        if col not in existing:
            cur.execute("ALTER TABLE events ADD COLUMN {} {}".format(col, col_type))
    conn.commit()


def _ensure_hotlist_schema(conn):
    required = {
        "timestamp":       "INTEGER",
        "address":         "TEXT",
        "symbol":          "TEXT",
        "action":          "TEXT",   # entry | confirmed | expired
        "buy_ratio":       "REAL",
        "volume":          "REAL",
        "trades":          "INTEGER",
        "pair_age":        "INTEGER",
        "market_cap":      "REAL",
        "signals_passed":  "INTEGER",
        "signal_sells":    "INTEGER",
        "signal_speed":    "INTEGER",
        "signal_vol":      "INTEGER",
        "notes":           "TEXT",
    }

    existing = _get_columns(conn, "hotlist_events")
    cur = conn.cursor()
    for col, col_type in required.items():
        if col not in existing:
            cur.execute("ALTER TABLE hotlist_events ADD COLUMN {} {}".format(col, col_type))
    conn.commit()


def _ensure_gem_candidates_schema(conn):
    required = {
        "timestamp":        "INTEGER",
        "address":          "TEXT",
        "symbol":           "TEXT",
        "checkpoint":       "TEXT",
        "score":            "REAL",
        "buy_volume_pct":   "REAL",
        "trade_speed":      "REAL",
        "recent_sells_pct": "REAL",
        "acceleration":     "REAL",
        "volume":           "REAL",
        "trades":           "INTEGER",
        "pair_age":         "INTEGER",
        "market_cap":       "REAL",
        "liquidity":        "REAL",
        "notes":            "TEXT",
    }

    existing = _get_columns(conn, "gem_candidates")
    cur = conn.cursor()
    for col, col_type in required.items():
        if col not in existing:
            cur.execute("ALTER TABLE gem_candidates ADD COLUMN {} {}".format(col, col_type))
    conn.commit()


# ── Schema initialisation ─────────────────────────────────────────────────────

def init_db():
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp        INTEGER,
        address          TEXT,
        symbol           TEXT,
        action           TEXT,
        reason           TEXT,
        checkpoint       TEXT,
        trades           INTEGER,
        volume           REAL,
        buys             INTEGER,
        sells            INTEGER,
        buy_volume       REAL,
        sell_volume      REAL,
        pair_age         INTEGER,
        market_cap       REAL,
        liquidity        REAL,
        trade_speed      REAL,
        recent_sells_pct REAL,
        score            REAL,
        detail           TEXT,
        buy_volume_pct   REAL,
        acceleration     REAL,
        vol_15           REAL,
        vol_60           REAL,
        speed_15         REAL,
        speed_60         REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS hotlist_events (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp      INTEGER,
        address        TEXT,
        symbol         TEXT,
        action         TEXT,
        buy_ratio      REAL,
        volume         REAL,
        trades         INTEGER,
        pair_age       INTEGER,
        market_cap     REAL,
        signals_passed INTEGER,
        signal_sells   INTEGER,
        signal_speed   INTEGER,
        signal_vol     INTEGER,
        notes          TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS gem_candidates (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp        INTEGER,
        address          TEXT,
        symbol           TEXT,
        checkpoint       TEXT,
        score            REAL,
        buy_volume_pct   REAL,
        trade_speed      REAL,
        recent_sells_pct REAL,
        acceleration     REAL,
        volume           REAL,
        trades           INTEGER,
        pair_age         INTEGER,
        market_cap       REAL,
        liquidity        REAL,
        notes            TEXT
    )
    """)

    conn.commit()

    # Migrate any pre-existing DB to the current schema.
    _ensure_events_schema(conn)
    _ensure_hotlist_schema(conn)
    _ensure_gem_candidates_schema(conn)

    conn.close()


# ── Insert helpers ────────────────────────────────────────────────────────────

def log_event(data):
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO events (
        timestamp, address, symbol, action, reason, checkpoint,
        trades, volume, buys, sells, buy_volume, sell_volume,
        pair_age, market_cap, liquidity, trade_speed,
        recent_sells_pct, score, detail,
        buy_volume_pct, acceleration,
        vol_15, vol_60, speed_15, speed_60
    ) VALUES (
        ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?,
        ?, ?,
        ?, ?, ?, ?
    )
    """, (
        int(time.time()),
        data.get("address"),
        data.get("symbol"),
        data.get("action"),
        data.get("reason"),
        data.get("checkpoint"),
        data.get("trades"),
        data.get("volume"),
        data.get("buys"),
        data.get("sells"),
        data.get("buy_volume"),
        data.get("sell_volume"),
        data.get("pair_age"),
        data.get("market_cap"),
        data.get("liquidity"),
        data.get("trade_speed"),
        data.get("recent_sells_pct"),
        data.get("score"),
        data.get("detail"),
        # momentum / CP5
        data.get("buy_volume_pct"),
        data.get("acceleration"),
        data.get("vol_15"),
        data.get("vol_60"),
        data.get("speed_15"),
        data.get("speed_60"),
    ))

    conn.commit()
    conn.close()


def log_hotlist_event(data):
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO hotlist_events (
        timestamp, address, symbol, action,
        buy_ratio, volume, trades, pair_age, market_cap,
        signals_passed, signal_sells, signal_speed, signal_vol, notes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(time.time()),
        data.get("address"),
        data.get("symbol"),
        data.get("action"),
        data.get("buy_ratio"),
        data.get("volume"),
        data.get("trades"),
        data.get("pair_age"),
        data.get("market_cap"),
        data.get("signals_passed", 0),
        data.get("signal_sells", 0),
        data.get("signal_speed", 0),
        data.get("signal_vol", 0),
        data.get("notes", ""),
    ))

    conn.commit()
    conn.close()


def log_gem_candidate(data):
    """
    Insert a CP5 gem-candidate event into gem_candidates.

    Expected keys in data (all optional — missing keys default to None / ""):
        address, symbol, checkpoint, score,
        buy_volume_pct, trade_speed, recent_sells_pct, acceleration,
        volume, trades, pair_age, market_cap, liquidity, notes
    """
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO gem_candidates (
        timestamp, address, symbol, checkpoint,
        score, buy_volume_pct, trade_speed, recent_sells_pct,
        acceleration, volume, trades, pair_age,
        market_cap, liquidity, notes
    ) VALUES (
        ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?
    )
    """, (
        int(time.time()),
        data.get("address"),
        data.get("symbol"),
        data.get("checkpoint"),
        data.get("score"),
        data.get("buy_volume_pct"),
        data.get("trade_speed"),
        data.get("recent_sells_pct"),
        data.get("acceleration"),
        data.get("volume"),
        data.get("trades"),
        data.get("pair_age"),
        data.get("market_cap"),
        data.get("liquidity"),
        data.get("notes", ""),
    ))

    conn.commit()
    conn.close()