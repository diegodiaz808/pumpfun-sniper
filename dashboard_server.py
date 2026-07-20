import sqlite3
from flask import Flask, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

STATE = {
    "tokens": {},
    "positions": {},
    "stats": {
        "scanned": 0,
        "bought": 0,
        "rugs": 0,
        "vol": 0,
    },
    "feed": [],
    "hotlist": {},
}


@app.route("/state")
def get_state():
    return jsonify(STATE)


def update_token(token):
    STATE["tokens"][token["address"]] = token


def update_position(token_mint, pos):
    STATE["positions"][token_mint] = pos


def increment_stat(key, val=1):
    STATE["stats"][key] += val


def push_feed(event_type, main, detail=""):
    STATE["feed"].insert(0, {
        "type": event_type,
        "main": main,
        "detail": detail,
    })
    STATE["feed"] = STATE["feed"][:200]


def update_hotlist(address, entry):
    STATE["hotlist"][address] = entry


def remove_hotlist(address):
    STATE["hotlist"].pop(address, None)


def get_db():
    return sqlite3.connect("analytics.db")


@app.route("/analytics")
def get_analytics():
    try:
        db = get_db()
        cur = db.execute("""
            SELECT
                timestamp, address, symbol, action, reason, checkpoint,
                trades, volume, buys, sells, buy_volume, sell_volume,
                pair_age, market_cap, liquidity, trade_speed,
                recent_sells_pct, score, detail
            FROM events
            ORDER BY id DESC
            LIMIT 200
        """)
        cols = [
            "timestamp", "address", "symbol", "action", "reason", "checkpoint",
            "trades", "volume", "buys", "sells", "buy_volume", "sell_volume",
            "pair_age", "market_cap", "liquidity", "trade_speed",
            "recent_sells_pct", "score", "detail"
        ]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        db.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analytics/summary")
def get_analytics_summary():
    try:
        db = get_db()

        by_action = {}
        for row in db.execute("SELECT action, COUNT(*) FROM events GROUP BY action"):
            by_action[row[0] or "unknown"] = row[1]

        by_reason = {}
        for row in db.execute("SELECT reason, COUNT(*) FROM events GROUP BY reason"):
            by_reason[row[0] or "unknown"] = row[1]

        by_checkpoint = {}
        for row in db.execute("SELECT checkpoint, COUNT(*) FROM events GROUP BY checkpoint"):
            by_checkpoint[row[0] or "none"] = row[1]

        latest = db.execute("""
            SELECT
                timestamp, address, symbol, action, reason, checkpoint,
                trades, volume, buys, sells, buy_volume, sell_volume,
                pair_age, market_cap, liquidity, trade_speed,
                recent_sells_pct, score, detail
            FROM events
            ORDER BY id DESC
            LIMIT 20
        """).fetchall()

        cols = [
            "timestamp", "address", "symbol", "action", "reason", "checkpoint",
            "trades", "volume", "buys", "sells", "buy_volume", "sell_volume",
            "pair_age", "market_cap", "liquidity", "trade_speed",
            "recent_sells_pct", "score", "detail"
        ]
        latest_rows = [dict(zip(cols, row)) for row in latest]

        # gem count — table may not exist on older DBs
        try:
            gem_count = db.execute("SELECT COUNT(*) FROM gem_candidates").fetchone()[0]
        except Exception:
            gem_count = None

        db.close()

        return jsonify({
            "by_action": by_action,
            "by_reason": by_reason,
            "by_checkpoint": by_checkpoint,
            "latest": latest_rows,
            "gem_count": gem_count,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analytics/export")
def export_analytics():
    try:
        db = get_db()
        cur = db.execute("""
            SELECT
                symbol, action, reason, checkpoint,
                trades, volume, buys, sells,
                buy_volume, sell_volume,
                pair_age, market_cap, liquidity,
                trade_speed, recent_sells_pct, score, detail
            FROM events
            ORDER BY id DESC
            LIMIT 1000
        """)

        lines = []
        for row in cur.fetchall():
            (
                symbol, action, reason, checkpoint,
                trades, volume, buys, sells,
                buy_volume, sell_volume,
                pair_age, market_cap, liquidity,
                trade_speed, recent_sells_pct, score, detail
            ) = row

            lines.append(
                "{} | {} | {} | cp={} | trades={} vol={:.4f} buys={} sells={} "
                "buyVol={} sellVol={} age={} mc={} liq={} speed={} recentSells={} score={} detail={}".format(
                    symbol,
                    action,
                    reason,
                    checkpoint,
                    trades if trades is not None else "n/a",
                    float(volume or 0),
                    buys if buys is not None else "n/a",
                    sells if sells is not None else "n/a",
                    buy_volume if buy_volume is not None else "n/a",
                    sell_volume if sell_volume is not None else "n/a",
                    pair_age if pair_age is not None else "n/a",
                    market_cap if market_cap is not None else "n/a",
                    liquidity if liquidity is not None else "n/a",
                    trade_speed if trade_speed is not None else "n/a",
                    recent_sells_pct if recent_sells_pct is not None else "n/a",
                    score if score is not None else "n/a",
                    detail if detail is not None else "",
                )
            )

        db.close()
        return Response("\n".join(lines), mimetype="text/plain")
    except Exception as e:
        return Response("error: {}".format(e), mimetype="text/plain", status=500)


@app.route("/analytics/hotlist")
def get_hotlist_history():
    try:
        db = get_db()
        cur = db.execute("""
            SELECT
                timestamp, address, symbol, action,
                buy_ratio, volume, trades, pair_age, market_cap,
                signals_passed, signal_sells, signal_speed, signal_vol, notes
            FROM hotlist_events
            ORDER BY id DESC
            LIMIT 200
        """)
        cols = [
            "timestamp", "address", "symbol", "action",
            "buy_ratio", "volume", "trades", "pair_age", "market_cap",
            "signals_passed", "signal_sells", "signal_speed", "signal_vol", "notes"
        ]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        db.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analytics/gems")
def get_gems():
    try:
        db = get_db()
        cur = db.execute("""
            SELECT
                timestamp, address, symbol, checkpoint,
                score, buy_volume_pct, trade_speed, recent_sells_pct,
                acceleration, volume, trades, pair_age,
                market_cap, liquidity, notes
            FROM gem_candidates
            ORDER BY id DESC
            LIMIT 100
        """)
        cols = [
            "timestamp", "address", "symbol", "checkpoint",
            "score", "buy_volume_pct", "trade_speed", "recent_sells_pct",
            "acceleration", "volume", "trades", "pair_age",
            "market_cap", "liquidity", "notes",
        ]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        db.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analytics/gems/export")
def export_gems():
    try:
        db = get_db()
        cur = db.execute("""
            SELECT
                symbol, address, score, acceleration,
                buy_volume_pct, trade_speed, recent_sells_pct,
                volume, trades, pair_age, market_cap
            FROM gem_candidates
            ORDER BY id DESC
            LIMIT 500
        """)

        def _fmt(v, fmt="{}", fallback="n/a"):
            return fmt.format(v) if v is not None else fallback

        lines = []
        for row in cur.fetchall():
            (
                symbol, address, score, acceleration,
                buy_volume_pct, trade_speed, recent_sells_pct,
                volume, trades, pair_age, market_cap,
            ) = row
            lines.append(
                "{} | {} | score={} | accel={} | buyVolPct={} | speed={} | "
                "recentSells={} | vol={} | trades={} | age={} | mc={}".format(
                    symbol  if symbol  is not None else "?",
                    address if address is not None else "?",
                    _fmt(score,            "{:.2f}"),
                    _fmt(acceleration,     "{:.3f}"),
                    _fmt(buy_volume_pct,   "{:.3f}"),
                    _fmt(trade_speed,      "{:.3f}"),
                    _fmt(recent_sells_pct, "{:.3f}"),
                    _fmt(volume,           "{:.4f}"),
                    _fmt(trades,           "{}"),
                    _fmt(pair_age,         "{}"),
                    _fmt(market_cap,       "{:.1f}"),
                )
            )

        db.close()
        return Response("\n".join(lines), mimetype="text/plain")
    except Exception as e:
        return Response("error: {}".format(e), mimetype="text/plain", status=500)


def run_server():
    app.run(port=5001)


if __name__ == "__main__":
    run_server()