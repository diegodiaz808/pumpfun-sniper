# Pumpfun Sniper — Solana Memecoin Bot

> Part of the JARVIS family: see [jarvis](https://github.com/diegodiaz808/jarvis) for the full AI trading assistant.

Autonomous trading bot for pump.fun launches on Solana. It watches new token pairs in real time, runs them through timed evaluation checkpoints with anti-rug analysis, executes entries via Jupiter, manages positions, and streams everything to a live dashboard.

**Runs with strict risk caps by design** (small position size, max exposure limit). Built as a personal research project — not financial advice, use at your own risk.

## Architecture

```
discovery/   PumpPortal WebSocket scanner (new pairs, live trades, auto-reconnect)
strategy/    anti-rug checks (holder concentration, LP, authority flags)
validation/  rug detector
analytics/   token risk enrichment, holder analysis, SQLite event logging
execution/   Jupiter swap execution (key loaded from .env, never committed)
portfolio/   position manager (entries, exits, exposure caps)
dashboard_server.py + memecoin_dashboard.html   live web dashboard
```

## Evaluation pipeline

Every new pair enters a watchlist and is evaluated at timed checkpoints:

| Checkpoint | Age | Filter |
|---|---|---|
| CP1 | 45 s | zombie filter (0 trades / 0 volume) |
| CP2 | 180 s | minimum trades + SOL volume |
| CP3 | 420 s | buy-pressure 1.5x + momentum (fast/slow window velocity + acceleration) |
| CP4 | 900 s | full evaluation — anti-rug + score → **BUY** |
| GEM | — | top-of-top: high acceleration + score → hotlist |

Momentum is computed over 15 s / 60 s rolling windows (velocity and acceleration of buys), and a shared scanner reference lets producer and consumer survive WebSocket reconnections without losing state.

## Risk controls

- Max 5 concurrent positions, 0.02 SOL per position, 0.1 SOL total exposure cap
- Anti-rug gate before any buy
- Every event logged to SQLite for post-trade analysis

## Stack

Python · asyncio · WebSockets (PumpPortal) · Jupiter API · Solana · SQLite · live HTML dashboard
