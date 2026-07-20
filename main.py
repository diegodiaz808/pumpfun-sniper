import asyncio
import threading
from typing import Dict, Set, List, Any

from config import settings
from discovery.pumpfun_scanner import PumpPortalScanner
from execution import jupiter_trader
from portfolio import position_manager
from strategy.anti_rug import anti_rug_check
from dashboard_server import update_token, update_position, increment_stat, run_server, push_feed
from analytics.token_enrichment import enrich_token_risk_data
from analytics.logger import init_db, log_event, log_hotlist_event, log_gem_candidate

watchlist: Dict[str, dict] = {}
processed: Set[str] = set()

# Tokens con momentum fuerte en observación extendida (hotlist)
hotlist: Dict[str, dict] = {}

# Ventanas de tiempo para métricas de momentum (segundos)
_WIN_FAST = 15
_WIN_SLOW = 60

# Referencia compartida al scanner activo — permite que producer y consumer
# usen siempre la instancia más reciente tras una reconexión
_scanner_ref: Dict[str, Any] = {"instance": None}


async def rebuild_scanner() -> PumpPortalScanner:
    """
    Crea un nuevo PumpPortalScanner, conecta y re-subscribe
    todos los tokens activos en watchlist.
    Watchlist y processed NO se limpian.
    """
    print("Scanner disconnected — rebuilding...")
    new_scanner = PumpPortalScanner()
    await new_scanner.connect()

    active = [addr for addr in watchlist if addr not in processed]
    for addr in active:
        try:
            await new_scanner.subscribe_token_trades(addr)
        except Exception as e:
            print("Re-subscribe error for {}: {}".format(addr, e))

    print("Scanner reconnected — Re-subscribed {} tokens".format(len(active)))
    push_feed("new", "Scanner reconnected",
              "re-subscribed {} tokens".format(len(active)))
    _scanner_ref["instance"] = new_scanner
    return new_scanner


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _init_watch_token(token: dict) -> dict:
    return {
        "address":        token["address"],
        "name":           token["name"],
        "symbol":         token["symbol"],
        "creator":        token["creator"],
        "created_at_ts":  token["created_at_ts"],
        "pair_age":       0,
        "volume":         0.0,
        "liquidity":      0.0,
        "trades":         0,
        "buys":           0,
        "sells":          0,
        "buy_volume":     0.0,
        "sell_volume":    0.0,
        "price_change":   0.0,
        "dev_wallet":     0.0,
        "largest_holder": 0.0,
        "top10":          0.0,
        "last_trade_ts":  None,
        "raw":            token["raw"],
        # ── momentum / orderflow ──
        "trade_speed":         0.0,
        "recent_sells_pct":    0.0,
        "recent_trades":       [],    # últimos 20 tipos "buy"/"sell"
        "recent_trade_events": [],    # [(ts, type, sol_amount), ...]
        "trades_15":           0,
        "trades_60":           0,
        "vol_15":              0.0,
        "vol_60":              0.0,
        "speed_15":            0.0,
        "speed_60":            0.0,
        "acceleration":        0.0,
        "buy_volume_pct":      0.0,
        # ── checkpoint state ──
        "cp4_passed":          False,
        "cp5_gem":             False,
        "hotlist_confirmed":   False,
    }


def _build_token_update(token, status, score=None):
    return {
        "address":  token["address"],
        "symbol":   token["symbol"],
        "name":     token["name"],
        "status":   status,
        "volume":   token["volume"],
        "trades":   token["trades"],
        "buys":     token["buys"],
        "sells":    token["sells"],
        "pair_age": token["pair_age"],
        "score":    score,
    }


def _build_position_update(pos, pnl_pct=None, amount_raw=None, closed=False):
    token_mint = pos["token_mint"]
    return {
        "symbol":       pos.get("symbol", token_mint[:6]),
        "entry_price":  pos.get("entry_price"),
        "amount_raw":   amount_raw if amount_raw is not None else pos.get("amount_raw"),
        "pnl_pct":      pnl_pct if pnl_pct is not None else 0.0,
        "tp1_done":     pos.get("tp1_done", False),
        "tp2_done":     pos.get("tp2_done", False),
        "tp3_done":     pos.get("tp3_done", False),
        "tp4_done":     pos.get("tp4_done", False),
        "moonbag_left": pos.get("moonbag_left", False),
        "closed":       closed,
    }


def _build_log(token, action, reason, checkpoint="", score=None, detail=""):
    """Payload enriquecido para log_event."""
    return {
        "address":          token["address"],
        "symbol":           token["symbol"],
        "action":           action,
        "reason":           reason,
        "checkpoint":       checkpoint,
        "trades":           token["trades"],
        "volume":           token["volume"],
        "buys":             token["buys"],
        "sells":            token["sells"],
        "buy_volume":       token.get("buy_volume", 0.0),
        "sell_volume":      token.get("sell_volume", 0.0),
        "pair_age":         token["pair_age"],
        "market_cap":       token["raw"].get("marketCapSol", 0),
        "liquidity":        token.get("liquidity", 0.0),
        "trade_speed":      token.get("trade_speed", 0.0),
        "recent_sells_pct": token.get("recent_sells_pct", 0.0),
        "score":            score,
        "detail":           detail,
        "buy_volume_pct":   token.get("buy_volume_pct", 0.0),
        "acceleration":     token.get("acceleration", 0.0),
    }


def _compute_exposure():
    open_positions   = position_manager.list_open_positions()
    total_exposure   = 0.0
    LAMPORTS_PER_SOL = 1_000_000_000
    for _pos in open_positions:
        ep  = _pos.get("entry_price")
        raw = _pos.get("amount_raw") or 0
        if ep and ep > 0 and raw > 0:
            total_exposure += (float(raw) * float(ep)) / LAMPORTS_PER_SOL
        else:
            total_exposure += settings.POSITION_SIZE
    return total_exposure


async def _execute_buy(token, address, score, checkpoint, reason):
    """Exposure check + compra + registro. Retorna True si ok."""
    total_exposure = _compute_exposure()

    if total_exposure >= settings.MAX_TOTAL_EXPOSURE_SOL:
        update_token(_build_token_update(token, "skip", score=score))
        push_feed("skip", "Skipped {}".format(token["symbol"]),
                  "max exposure {:.3f}/{:.3f} SOL".format(
                      total_exposure, settings.MAX_TOTAL_EXPOSURE_SOL))
        log_event(_build_log(token, "skip", "max_exposure_reached",
                             checkpoint=checkpoint, score=score))
        return False

    remaining_exposure = settings.MAX_TOTAL_EXPOSURE_SOL - total_exposure
    trade_size = max(0.001, round(min(settings.POSITION_SIZE, remaining_exposure), 4))

    print("EXPOSURE | {:.4f}/{:.4f} SOL | trade_size={:.4f}".format(
        total_exposure, settings.MAX_TOTAL_EXPOSURE_SOL, trade_size))

    buy_result = jupiter_trader.buy_token(token["address"], trade_size_sol=trade_size)

    if not buy_result.get("ok"):
        push_feed("skip", "Buy failed {}".format(token["symbol"]),
                  str(buy_result.get("error", "unknown"))[:80])
        return False

    entry_price = None
    amount_raw  = buy_result.get("out_amount_raw")
    quote       = buy_result.get("quote") or {}
    in_amount   = quote.get("inAmount")
    out_amount  = quote.get("outAmount")
    try:
        if in_amount and out_amount and float(out_amount) > 0:
            entry_price = float(in_amount) / float(out_amount)
    except Exception:
        entry_price = None

    position_manager.add_position(
        token["address"],
        entry_price=entry_price,
        amount_raw=amount_raw,
        symbol=token.get("symbol", ""),
    )
    increment_stat("bought")
    update_token(_build_token_update(token, "buy", score=score))
    update_position(token["address"], {
        "symbol":       token["symbol"],
        "entry_price":  entry_price,
        "amount_raw":   amount_raw,
        "pnl_pct":      0.0,
        "tp1_done":     False,
        "tp2_done":     False,
        "tp3_done":     False,
        "tp4_done":     False,
        "moonbag_left": False,
        "closed":       False,
    })
    push_feed("buy", "Position opened {}".format(token["symbol"]),
              "entry={} @{}".format(entry_price or "n/a", checkpoint))
    log_event(_build_log(token, "buy", reason, checkpoint=checkpoint,
                         score=score,
                         detail="entry_price={}".format(entry_price or "n/a")))
    return True


async def _skip_token(token, address, reason, checkpoint, scanner, score=None, detail=""):
    """Loguea skip, actualiza dashboard y remueve del watchlist."""
    update_token(_build_token_update(token, "skip", score=score))
    push_feed("skip", "Skipped {}".format(token["symbol"]),
              "{} @{}".format(reason, checkpoint))
    log_event(_build_log(token, "skip", reason, checkpoint=checkpoint,
                         score=score, detail=detail))
    processed.add(address)
    watchlist.pop(address, None)
    await scanner.unsubscribe_token_trades(address)


# ── PRODUCER ──────────────────────────────────────────────────────────────────
async def producer(scanner: PumpPortalScanner) -> None:
    _scanner_ref["instance"] = scanner

    while True:
        current = _scanner_ref["instance"]
        try:
            event = await current.recv_event()
            if not event:
                continue

            if event["type"] == "new_token":
                address = event["address"]
                if address in watchlist:
                    continue

                watchlist[address] = _init_watch_token(event)
                increment_stat("scanned")

                update_token({
                    "address":  address,
                    "symbol":   event["symbol"],
                    "name":     event["name"],
                    "status":   "watch",
                    "volume":   0.0,
                    "trades":   0,
                    "buys":     0,
                    "sells":    0,
                    "pair_age": 0,
                    "score":    None,
                })

                push_feed("new", "New token {}".format(event["symbol"]),
                          "{}".format(address[:8]))
                print("NEW TOKEN | {} | {} | {} | creator={}".format(
                    event["symbol"], event["name"], address, event["creator"]))

                await current.subscribe_token_trades(address)

            elif event["type"] == "trade":
                address = event["address"]
                token   = watchlist.get(address)
                if not token:
                    continue

                now        = asyncio.get_event_loop().time()
                trade_type = event.get("trade_type", "buy")
                sol_amount = event["sol_amount"]

                token["last_trade_ts"] = now
                token["trades"]        += 1
                token["volume"]        += sol_amount
                token["pair_age"]       = int(now - token["created_at_ts"])
                increment_stat("vol", sol_amount)

                position_manager.update_last_trade_ts(address, trade_ts=now)

                if trade_type == "buy":
                    token["buys"]       += 1
                    token["buy_volume"] += sol_amount
                elif trade_type == "sell":
                    token["sells"]       += 1
                    token["sell_volume"] += sol_amount

                # ── ventana últimos 20 tipos ──
                token["recent_trades"].append(trade_type)
                if len(token["recent_trades"]) > 20:
                    token["recent_trades"].pop(0)
                window = token["recent_trades"]
                if window:
                    token["recent_sells_pct"] = window.count("sell") / float(len(window))

                # ── recent_trade_events con timestamp ──
                token["recent_trade_events"].append((now, trade_type, sol_amount))
                cutoff = now - _WIN_SLOW
                token["recent_trade_events"] = [
                    e for e in token["recent_trade_events"] if e[0] >= cutoff
                ]

                # ── métricas de ventana temporal ──
                events      = token["recent_trade_events"]
                fast_cutoff = now - _WIN_FAST

                trades_fast = sum(1 for e in events if e[0] >= fast_cutoff)
                trades_slow = len(events)
                vol_fast    = sum(e[2] for e in events if e[0] >= fast_cutoff)
                vol_slow    = sum(e[2] for e in events)

                token["trades_15"] = trades_fast
                token["trades_60"] = trades_slow
                token["vol_15"]    = vol_fast
                token["vol_60"]    = vol_slow

                speed_fast = trades_fast / float(_WIN_FAST)
                speed_slow = trades_slow / float(_WIN_SLOW)
                token["speed_15"]    = speed_fast
                token["speed_60"]    = speed_slow
                token["trade_speed"] = speed_fast

                token["acceleration"] = (speed_fast / speed_slow) if speed_slow > 0 else 0.0

                total_vol = token["buy_volume"] + token["sell_volume"]
                token["buy_volume_pct"] = (token["buy_volume"] / total_vol) if total_vol > 0 else 0.0

                update_token(_build_token_update(token, "watch"))

        except Exception as exc:
            err = str(exc)
            if "close frame" in err or "ConnectionClosed" in err or "WebSocket" in err:
                print("Producer error (disconnected): {}".format(exc))
                try:
                    _scanner_ref["instance"] = await rebuild_scanner()
                except Exception as rebuild_exc:
                    print("Rebuild failed: {} — retrying in 5s".format(rebuild_exc))
                    await asyncio.sleep(5)
            else:
                print("Producer error: {}".format(exc))
                await asyncio.sleep(2)


# ── CONSUMER ──────────────────────────────────────────────────────────────────
async def consumer(scanner: PumpPortalScanner) -> None:
    print("Memecoin Bot started")

    while True:
        try:
            # Usar siempre la instancia activa (puede haber cambiado tras reconexión)
            current_scanner = _scanner_ref.get("instance") or scanner
            now = asyncio.get_event_loop().time()

            for address, token in list(watchlist.items()):
                if address in processed:
                    continue

                token["pair_age"] = int(now - token["created_at_ts"])
                age  = token["pair_age"]
                bvp  = token.get("buy_volume_pct", 0.0)
                accel = token.get("acceleration", 0.0)
                sells = token.get("recent_sells_pct", 0.0)

                print(
                    "WATCHING | {} | age={}s | trades={} | vol={:.3f} | "
                    "bvp={:.2f} | accel={:.2f} | sells={:.2f}".format(
                        token["symbol"], age, token["trades"], token["volume"],
                        bvp, accel, sells,
                    )
                )

                # Antes del CP1 → demasiado nuevo
                if age < settings.PAIR_AGE_CP1:
                    continue

                # En hotlist esperando confirmación → no procesar
                if address in hotlist and not token.get("hotlist_confirmed"):
                    continue

                # ── TIMEOUT DEFINITIVO (25 min) ──────────────────────────────
                if age > settings.PAIR_AGE_MAX:
                    total_tr  = max(token["trades"], 1)
                    buy_ratio = token["buys"] / float(total_tr)
                    if buy_ratio < 0.65 or token["volume"] < 5.0:
                        log_event(_build_log(token, "skip", "too_old",
                                             checkpoint="TIMEOUT"))
                        update_token(_build_token_update(token, "skip"))
                        push_feed("skip", "Skipped {}".format(token["symbol"]),
                                  "timeout 25min")
                        processed.add(address)
                        watchlist.pop(address, None)
                        await current_scanner.unsubscribe_token_trades(address)
                    continue

                # ── CP1 (45s): solo zombies ──────────────────────────────────
                if age < settings.PAIR_AGE_CP2:
                    if token["trades"] == 0 and token["volume"] == 0.0:
                        await _skip_token(token, address, "zombie", "CP1", current_scanner)
                    continue

                # ── CP2 (3min): observar, no descartar ───────────────────────
                if age < settings.PAIR_AGE_CP3:
                    continue

                # ── CP3 (7min): momentum pre-filter ──────────────────────────
                if age < settings.PAIR_AGE_CP4:
                    if token["trades"] < settings.MIN_TRADES:
                        await _skip_token(token, address, "low_trades", "CP3", current_scanner)
                        continue
                    if token["volume"] < settings.MIN_VOLUME_SOL:
                        await _skip_token(token, address, "low_volume", "CP3", current_scanner)
                        continue
                    if token["buys"] < token["sells"] * 1.5:
                        await _skip_token(token, address, "weak_buy_pressure", "CP3", current_scanner)
                        continue
                    if bvp < settings.CP3_MIN_BUY_VOLUME_PCT:
                        await _skip_token(token, address, "low_buy_volume_pct", "CP3",
                                          scanner, detail="bvp={:.2f}".format(bvp))
                        continue
                    if accel < settings.CP3_MIN_ACCELERATION:
                        await _skip_token(token, address, "low_acceleration", "CP3",
                                          scanner, detail="accel={:.2f}".format(accel))
                        continue
                    if sells > settings.CP3_MAX_RECENT_SELLS_PCT:
                        await _skip_token(token, address, "high_sells_pct", "CP3",
                                          scanner, detail="sells={:.2f}".format(sells))
                        continue
                    continue   # pasó CP3, esperar CP4

                # ── CP4 (15min): evaluación completa ─────────────────────────
                if token["trades"] < settings.MIN_TRADES:
                    await _skip_token(token, address, "low_trades", "CP4", current_scanner)
                    continue
                if token["volume"] < settings.MIN_VOLUME_SOL:
                    await _skip_token(token, address, "low_volume", "CP4", current_scanner)
                    continue
                if token["buys"] < token["sells"] * 1.5:
                    await _skip_token(token, address, "weak_buy_pressure", "CP4", current_scanner)
                    continue
                if bvp < settings.CP4_MIN_BUY_VOLUME_PCT:
                    await _skip_token(token, address, "low_buy_volume_pct", "CP4",
                                      scanner, detail="bvp={:.2f}".format(bvp))
                    continue
                if accel < settings.CP4_MIN_ACCELERATION:
                    await _skip_token(token, address, "low_acceleration", "CP4",
                                      scanner, detail="accel={:.2f}".format(accel))
                    continue
                if sells > settings.CP4_MAX_RECENT_SELLS_PCT:
                    await _skip_token(token, address, "high_sells_pct", "CP4",
                                      scanner, detail="sells={:.2f}".format(sells))
                    continue

                # Anti-rug CP4
                risk_payload = enrich_token_risk_data(token)
                risk_payload["trade_speed"]      = token.get("trade_speed", 0.0)
                risk_payload["recent_sells_pct"] = sells
                anti_ok, anti_reasons = anti_rug_check(risk_payload)

                if not anti_ok:
                    print("ANTI-RUG FAIL @CP4 | {} | {}".format(address, anti_reasons))
                    increment_stat("rugs")
                    push_feed("rug", "Rug blocked {}".format(token["symbol"]),
                              ",".join(anti_reasons))
                    update_token(_build_token_update(token, "rug"))
                    log_event(_build_log(token, "antirug_fail",
                                         ",".join(anti_reasons), checkpoint="CP4"))
                    processed.add(address)
                    watchlist.pop(address, None)
                    await current_scanner.unsubscribe_token_trades(address)
                    continue

                # Scoring CP4
                if sells >= 0.50:
                    await _skip_token(token, address, "sell_spike_veto", "CP4", current_scanner)
                    continue

                trade_speed = token.get("trade_speed", 0.0)
                bvp_pts = 40 if bvp >= 0.75 else (0 if bvp <= 0.55 else
                          int(((bvp - 0.55) / 0.20) * 40))
                spd_pts = 35 if trade_speed >= 0.4 else (0 if trade_speed <= 0.06 else
                          int(((trade_speed - 0.06) / 0.34) * 35))
                sel_pts = 25 if sells <= 0.25 else max(
                          0, int(((0.50 - sells) / 0.25) * 25))
                score   = bvp_pts + spd_pts + sel_pts

                print("SCORE @CP4 | {} | score={} (bvp={} spd={} sel={}) "
                      "accel={:.2f} bvp={:.2f}".format(
                          address, score, bvp_pts, spd_pts, sel_pts, accel, bvp))

                if score < settings.CP4_MIN_SCORE:
                    await _skip_token(token, address, "low_score", "CP4",
                                      scanner, score=score)
                    continue

                # ── CP4 PASADO ────────────────────────────────────────────────
                token["cp4_passed"] = True
                log_event(_build_log(token, "cp4_pass", "passed_cp4",
                                     checkpoint="CP4", score=score))

                # ── HOTLIST check ─────────────────────────────────────────────
                total_tr  = max(token["trades"], 1)
                buy_ratio = token["buys"] / float(total_tr)

                if (
                    buy_ratio >= settings.HOTLIST_MIN_BUY_RATIO
                    and token["volume"] >= settings.HOTLIST_MIN_VOLUME_SOL
                    and token["trades"] >= settings.HOTLIST_MIN_TRADES
                    and address not in hotlist
                    and address not in processed
                ):
                    observe_until = now + settings.HOTLIST_OBSERVE_SEC
                    hotlist[address] = {
                        "token":           token,
                        "observe_until":   observe_until,
                        "vol_at_entry":    token["volume"],
                        "vol_samples":     [],
                        "symbol":          token["symbol"],
                        "buy_ratio_entry": buy_ratio,
                    }
                    print("HOTLIST ENTRY | {} | ratio={:.2f} vol={:.1f}".format(
                        token["symbol"], buy_ratio, token["volume"]))
                    push_feed("new", "Hotlist: {}".format(token["symbol"]),
                              "ratio={:.0f}% vol={:.1f}SOL".format(
                                  buy_ratio * 100, token["volume"]))
                    log_hotlist_event({
                        "address":    address,
                        "symbol":     token["symbol"],
                        "action":     "entry",
                        "buy_ratio":  buy_ratio,
                        "volume":     token["volume"],
                        "trades":     token["trades"],
                        "pair_age":   token["pair_age"],
                        "market_cap": token["raw"].get("marketCapSol", 0),
                        "notes":      "observing for {}s".format(settings.HOTLIST_OBSERVE_SEC),
                    })
                    continue

                # ── CP5: gem detector ─────────────────────────────────────────
                is_cp5 = (
                    token.get("cp4_passed", False)
                    and bvp       >= settings.CP5_MIN_BUY_VOLUME_PCT
                    and accel     >= settings.CP5_MIN_ACCELERATION
                    and sells     <= settings.CP5_MAX_RECENT_SELLS_PCT
                    and trade_speed >= settings.CP5_MIN_TRADE_SPEED
                    and score       >= settings.CP5_MIN_SCORE
                )

                if is_cp5:
                    token["cp5_gem"] = True
                    update_token(_build_token_update(token, "gem", score=score))
                    push_feed("buy", "💎 GEM {}".format(token["symbol"]),
                              "score={} accel={:.2f} bvp={:.2f}".format(
                                  score, accel, bvp))
                    log_event(_build_log(token, "cp5_gem", "gem_detected",
                                         checkpoint="CP5", score=score,
                                         detail="accel={:.2f} bvp={:.2f}".format(accel, bvp)))
                    log_gem_candidate({
                        "address":          address,
                        "symbol":           token["symbol"],
                        "score":            score,
                        "buy_volume_pct":   bvp,
                        "acceleration":     accel,
                        "trade_speed":      trade_speed,
                        "recent_sells_pct": sells,
                        "volume":           token["volume"],
                        "trades":           token["trades"],
                        "pair_age":         age,
                        "market_cap":       token["raw"].get("marketCapSol", 0),
                    })
                    print("💎 CP5 GEM | {} | score={} | BUYING NOW".format(
                        token["symbol"], score))

                    await _execute_buy(token, address, score, "CP5", "cp5_gem_buy")
                    processed.add(address)
                    watchlist.pop(address, None)
                    await current_scanner.unsubscribe_token_trades(address)
                    continue

                # ── CP4 NORMAL BUY ────────────────────────────────────────────
                print("BUY SIGNAL @CP4 | {} | score={}".format(address, score))
                push_feed("buy", "Buy {}".format(token["symbol"]),
                          "score={} @CP4".format(score))

                await _execute_buy(token, address, score, "CP4", "passed_all_filters")
                processed.add(address)
                watchlist.pop(address, None)
                await current_scanner.unsubscribe_token_trades(address)

        except Exception as exc:
            print("Consumer error: {}".format(exc))

        await asyncio.sleep(2)


# ── POSITION MONITOR ──────────────────────────────────────────────────────────
async def position_monitor_loop() -> None:
    while True:
        try:
            open_positions = position_manager.list_open_positions()

            for pos in open_positions:
                token_mint  = pos["token_mint"]
                entry_price = pos.get("entry_price")
                amount_raw  = pos.get("amount_raw")
                sym         = pos.get("symbol", token_mint[:6])

                if entry_price is None or amount_raw is None:
                    continue

                # ── PRECIO ACTUAL ────────────────────────────────────────────
                quote      = jupiter_trader._get_sell_quote(token_mint, amount_raw, slippage_bps=100)
                out_amount = quote.get("outAmount")
                if not out_amount:
                    continue

                current_price = float(out_amount) / float(amount_raw)
                pnl_pct       = (current_price / float(entry_price)) - 1.0
                initial       = pos.get("initial_amount_raw") or amount_raw

                print("[POS CHECK] {} pnl={:.2f}% tp1={} tp2={} tp3={} tp4={} sl={:.2f}x".format(
                    sym, pnl_pct * 100,
                    pos.get("tp1_done"), pos.get("tp2_done"),
                    pos.get("tp3_done"), pos.get("tp4_done"),
                    pos.get("sl_floor_mult", 0.80),
                ))

                update_position(token_mint, _build_position_update(pos, pnl_pct=pnl_pct))

                watched   = watchlist.get(token_mint)
                spike_pct = watched.get("recent_sells_pct", 0.0) if watched else 0.0
                w_accel   = watched.get("acceleration", 1.0) if watched else 1.0

                # ── SELL SPIKE EXIT ──────────────────────────────────────────
                if spike_pct >= 0.65:
                    print("[SELL SPIKE EXIT] {} spike={:.2f}%".format(sym, spike_pct * 100))
                    sell_result = jupiter_trader.sell_token(
                        token_mint, raw_amount=amount_raw, sell_pct=1.0)
                    if sell_result.get("ok"):
                        position_manager.mark_closed(token_mint)
                        pos["closed"]     = True
                        pos["amount_raw"] = 0
                        update_position(token_mint, _build_position_update(
                            pos, pnl_pct=pnl_pct, amount_raw=0, closed=True))
                        label = "no TP1" if not pos.get("tp1_done") else "TP1 done"
                        push_feed("trade", "Sell spike exit {}".format(sym),
                                  "spike={:.0f}% | {}".format(spike_pct * 100, label))
                    continue

                # ── MOMENTUM LOSS EXIT ────────────────────────────────────────
                if w_accel < 0.70 and spike_pct >= 0.60:
                    print("[MOMENTUM LOSS] {} accel={:.2f} sells={:.2f}".format(
                        sym, w_accel, spike_pct))
                    sell_result = jupiter_trader.sell_token(
                        token_mint, raw_amount=amount_raw, sell_pct=1.0)
                    if sell_result.get("ok"):
                        position_manager.mark_closed(token_mint)
                        pos["closed"]     = True
                        pos["amount_raw"] = 0
                        update_position(token_mint, _build_position_update(
                            pos, pnl_pct=pnl_pct, amount_raw=0, closed=True))
                        label = "no TP1 - full exit" if not pos.get("tp1_done") else "protect capital"
                        push_feed("trade", "Momentum loss exit {}".format(sym),
                                  "{} | accel={:.2f}".format(label, w_accel))
                    continue

                # ── STOP LOSS DINÁMICO ───────────────────────────────────────
                if position_manager.should_stop_loss(pos, pnl_pct):
                    print("[STOP LOSS] {} pnl={:.2f}%".format(sym, pnl_pct * 100))
                    sell_result = jupiter_trader.sell_token(
                        token_mint, raw_amount=amount_raw, sell_pct=1.0)
                    if sell_result.get("ok"):
                        position_manager.mark_closed(token_mint)
                        pos["closed"]     = True
                        pos["amount_raw"] = 0
                        update_position(token_mint, _build_position_update(
                            pos, pnl_pct=pnl_pct, amount_raw=0, closed=True))
                        push_feed("trade", "SL hit {}".format(sym),
                                  "pnl={:.2f}%".format(pnl_pct * 100))
                    continue

                # ── TP1: +25% → vende 30% ────────────────────────────────────
                if position_manager.should_take_profit_1(pos, pnl_pct):
                    print("[TP1] {} pnl={:.2f}%".format(sym, pnl_pct * 100))
                    tp1_raw     = int(int(initial) * position_manager.TP1_SELL_FRAC)
                    sell_result = jupiter_trader.sell_token(
                        token_mint, raw_amount=tp1_raw, sell_pct=1.0)
                    if sell_result.get("ok"):
                        remaining         = int(int(initial) * (1.0 - position_manager.TP1_SELL_FRAC))
                        pos["amount_raw"] = remaining
                        pos["tp1_done"]   = True
                        position_manager.mark_tp1_done(token_mint)
                        update_position(token_mint, _build_position_update(
                            pos, pnl_pct=pnl_pct, amount_raw=remaining))
                        push_feed("trade", "TP1 {} +25%".format(sym),
                                  "sold 30% | SL → break-even")
                    continue

                # ── TP2: +50% → vende 20% ────────────────────────────────────
                if position_manager.should_take_profit_2(pos, pnl_pct):
                    print("[TP2] {} pnl={:.2f}%".format(sym, pnl_pct * 100))
                    tp2_raw     = int(int(initial) * position_manager.TP2_SELL_FRAC)
                    sell_result = jupiter_trader.sell_token(
                        token_mint, raw_amount=tp2_raw, sell_pct=1.0)
                    if sell_result.get("ok"):
                        remaining         = max(0, pos["amount_raw"] - tp2_raw)
                        pos["amount_raw"] = remaining
                        pos["tp2_done"]   = True
                        position_manager.mark_tp2_done(token_mint)
                        update_position(token_mint, _build_position_update(
                            pos, pnl_pct=pnl_pct, amount_raw=remaining))
                        push_feed("trade", "TP2 {} +50%".format(sym),
                                  "sold 20% | SL → +25%")
                    continue

                # ── TP3: +100% → vende 35% ───────────────────────────────────
                if position_manager.should_take_profit_3(pos, pnl_pct):
                    print("[TP3] {} pnl={:.2f}%".format(sym, pnl_pct * 100))
                    tp3_raw     = int(int(initial) * position_manager.TP3_SELL_FRAC)
                    sell_result = jupiter_trader.sell_token(
                        token_mint, raw_amount=tp3_raw, sell_pct=1.0)
                    if sell_result.get("ok"):
                        remaining         = max(0, pos["amount_raw"] - tp3_raw)
                        pos["amount_raw"] = remaining
                        pos["tp3_done"]   = True
                        position_manager.mark_tp3_done(token_mint)
                        update_position(token_mint, _build_position_update(
                            pos, pnl_pct=pnl_pct, amount_raw=remaining))
                        push_feed("trade", "TP3 {} +100%".format(sym),
                                  "sold 35% | SL → +50%")
                    continue

                # ── TP4: +200% → vende 10%, moonbag 5% ───────────────────────
                if position_manager.should_take_profit_4(pos, pnl_pct):
                    print("[TP4] {} pnl={:.2f}%".format(sym, pnl_pct * 100))
                    tp4_raw     = int(int(initial) * position_manager.TP4_SELL_FRAC)
                    sell_result = jupiter_trader.sell_token(
                        token_mint, raw_amount=tp4_raw, sell_pct=1.0)
                    if sell_result.get("ok"):
                        remaining           = max(0, pos["amount_raw"] - tp4_raw)
                        pos["amount_raw"]   = remaining
                        pos["tp4_done"]     = True
                        pos["moonbag_left"] = True
                        position_manager.mark_tp4_done(token_mint)
                        update_position(token_mint, _build_position_update(
                            pos, pnl_pct=pnl_pct, amount_raw=remaining))
                        push_feed("trade", "TP4 {} +200%".format(sym),
                                  "sold 10% | moonbag holding")
                    continue

                # ── INACTIVIDAD ───────────────────────────────────────────────
                if position_manager.should_inactivity_exit(pos):
                    print("[INACTIVITY EXIT] {}".format(sym))
                    sell_result = jupiter_trader.sell_token(
                        token_mint, raw_amount=amount_raw, sell_pct=1.0)
                    if sell_result.get("ok"):
                        position_manager.mark_closed(token_mint)
                        pos["closed"]     = True
                        pos["amount_raw"] = 0
                        update_position(token_mint, _build_position_update(
                            pos, pnl_pct=pnl_pct, amount_raw=0, closed=True))
                        push_feed("trade", "Inactivity exit {}".format(sym),
                                  "no trades for too long")
                    continue

                # ── TIMEOUT: sin TP1 → salida total ─────────────────────────
                if position_manager.should_timeout_full_exit(pos):
                    print("[TIMEOUT EXIT] {}".format(sym))
                    sell_result = jupiter_trader.sell_token(
                        token_mint, raw_amount=amount_raw, sell_pct=1.0)
                    if sell_result.get("ok"):
                        position_manager.mark_closed(token_mint)
                        pos["closed"]     = True
                        pos["amount_raw"] = 0
                        update_position(token_mint, _build_position_update(
                            pos, pnl_pct=pnl_pct, amount_raw=0, closed=True))
                        push_feed("trade", "Timeout exit {}".format(sym),
                                  "full exit | no TP in 1h")
                    continue

                # ── TIMEOUT: moonbag hold ─────────────────────────────────────
                if position_manager.should_timeout_keep_moonbag(pos):
                    print("[MOONBAG HOLD] {} raw={}".format(sym, pos["amount_raw"]))
                    continue

        except Exception as exc:
            print("Position monitor error: {}".format(exc))

        await asyncio.sleep(10)


# ── HOTLIST MONITOR ───────────────────────────────────────────────────────────
async def hotlist_monitor_loop() -> None:
    while True:
        try:
            now = asyncio.get_event_loop().time()

            for address, entry in list(hotlist.items()):
                token = watchlist.get(address)
                if not token:
                    hotlist.pop(address, None)
                    continue

                entry["vol_samples"].append((now, token["volume"]))
                cutoff = now - settings.HOTLIST_OBSERVE_SEC
                entry["vol_samples"] = [
                    (t, v) for t, v in entry["vol_samples"] if t >= cutoff]

                if now < entry["observe_until"]:
                    remaining = int(entry["observe_until"] - now)
                    print("HOTLIST WATCH | {} | {}s remaining | "
                          "speed={:.3f} sells={:.2f} accel={:.2f}".format(
                              token["symbol"], remaining,
                              token.get("trade_speed", 0),
                              token.get("recent_sells_pct", 0),
                              token.get("acceleration", 0)))
                    continue

                # ── PERÍODO TERMINADO ────────────────────────────────────────
                sym = token["symbol"]

                s1 = 1 if token.get("recent_sells_pct", 1.0) < settings.HOTLIST_MAX_SELLS_PCT else 0
                s2 = 1 if token.get("trade_speed", 0.0) >= settings.HOTLIST_MIN_SPEED else 0

                s3 = 0
                samples = entry["vol_samples"]
                if len(samples) >= 2:
                    recent_cutoff = now - 120
                    recent_vols   = [v for t, v in samples if t >= recent_cutoff]
                    avg_vol       = (samples[-1][1] - samples[0][1]) / max(len(samples) - 1, 1)
                    recent_delta  = (recent_vols[-1] - recent_vols[0]) if len(recent_vols) >= 2 else 0
                    if avg_vol > 0 and recent_delta >= avg_vol * (1 + settings.HOTLIST_VOL_GROWTH_PCT):
                        s3 = 1

                signals_passed = s1 + s2 + s3
                total_tr       = max(token["trades"], 1)
                buy_ratio      = token["buys"] / float(total_tr)

                print("HOTLIST EVAL | {} | signals={}/3 | ratio={:.2f}".format(
                    sym, signals_passed, buy_ratio))

                if signals_passed >= 2:
                    print("HOTLIST CONFIRMED | {} → buy flow".format(sym))
                    push_feed("buy", "Hotlist confirmed: {}".format(sym),
                              "signals={}/3 ratio={:.0f}%".format(
                                  signals_passed, buy_ratio * 100))

                    log_hotlist_event({
                        "address":        address, "symbol": sym,
                        "action":         "confirmed",
                        "buy_ratio":      buy_ratio,
                        "volume":         token["volume"],
                        "trades":         token["trades"],
                        "pair_age":       token["pair_age"],
                        "market_cap":     token["raw"].get("marketCapSol", 0),
                        "signals_passed": signals_passed,
                        "signal_sells":   s1, "signal_speed": s2, "signal_vol": s3,
                        "notes":          "confirmed after {}s".format(settings.HOTLIST_OBSERVE_SEC),
                    })

                    hotlist.pop(address, None)

                    # Anti-rug final
                    risk_payload = enrich_token_risk_data(token)
                    risk_payload["trade_speed"]      = token.get("trade_speed", 0.0)
                    risk_payload["recent_sells_pct"] = token.get("recent_sells_pct", 0.0)
                    anti_ok, anti_reasons = anti_rug_check(risk_payload)

                    if not anti_ok:
                        push_feed("rug", "Hotlist rug blocked: {}".format(sym),
                                  ",".join(anti_reasons))
                        log_event(_build_log(token, "antirug_fail",
                                             ",".join(anti_reasons),
                                             checkpoint="HOTLIST"))
                        processed.add(address)
                        watchlist.pop(address, None)
                        continue

                    await _execute_buy(token, address, None, "HOTLIST", "hotlist_confirmed")
                    processed.add(address)
                    watchlist.pop(address, None)

                else:
                    print("HOTLIST EXPIRED | {} | signals={}/3".format(sym, signals_passed))
                    push_feed("skip", "Hotlist expired: {}".format(sym),
                              "{}/3 signals".format(signals_passed))
                    log_hotlist_event({
                        "address":        address, "symbol": sym,
                        "action":         "expired",
                        "buy_ratio":      buy_ratio,
                        "volume":         token["volume"],
                        "trades":         token["trades"],
                        "pair_age":       token["pair_age"],
                        "market_cap":     token["raw"].get("marketCapSol", 0),
                        "signals_passed": signals_passed,
                        "signal_sells":   s1, "signal_speed": s2, "signal_vol": s3,
                        "notes":          "momentum not confirmed",
                    })
                    log_event(_build_log(token, "skip", "hotlist_expired",
                                         checkpoint="HOTLIST"))
                    hotlist.pop(address, None)
                    processed.add(address)
                    watchlist.pop(address, None)

        except Exception as exc:
            print("Hotlist monitor error: {}".format(exc))

        await asyncio.sleep(30)


# ── MAIN ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    scanner = PumpPortalScanner()

    init_db()

    threading.Thread(target=run_server, daemon=True).start()

    await asyncio.gather(
        producer(scanner),
        consumer(scanner),
        position_monitor_loop(),
        hotlist_monitor_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())