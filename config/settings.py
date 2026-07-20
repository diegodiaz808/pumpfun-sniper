PAIR_AGE_MAX = 1500    # 25 min → timeout definitivo

# ── CHECKPOINTS DE EVALUACIÓN ─────────────────────────────────────────────────
# CP1 ( 45s): solo filtra zombies (0 trades, 0 vol)
# CP2 (180s): filtros básicos — MIN_TRADES + MIN_VOLUME_SOL
# CP3 (420s): básicos + buy pressure 1.5× + momentum
# CP4 (900s): evaluación completa — anti-rug + score → BUY si pasa
# CP5 (gem): top-of-top — alta aceleración + score → GEM
PAIR_AGE_CP1 = 45
PAIR_AGE_CP2 = 180
PAIR_AGE_CP3 = 420
PAIR_AGE_CP4 = 900

MIN_TRADES = 8
MIN_VOLUME_SOL = 1.5

MAX_POSITIONS = 5
POSITION_SIZE = 0.02
MAX_TOTAL_EXPOSURE_SOL = 0.1

SCAN_INTERVAL = 3

# ── MOMENTUM ──────────────────────────────────────────────────────────────────
# Ventanas de tiempo para calcular velocidad y aceleración de compras
MOMENTUM_WINDOW_FAST_SEC = 15   # ventana rápida (señal inmediata)
MOMENTUM_WINDOW_SLOW_SEC = 60   # ventana lenta  (baseline de comparación)

# ── CP3 MOMENTUM THRESHOLDS (420s) ────────────────────────────────────────────
# Filtros adicionales de momentum sobre el buy-pressure 1.5× ya existente
CP3_MIN_BUY_VOLUME_PCT   = 0.60  # ≥60% del volumen debe ser compras
CP3_MIN_ACCELERATION     = 1.15  # velocidad fast/slow ≥ 1.15×
CP3_MAX_RECENT_SELLS_PCT = 0.40  # ventas recientes < 40%

# ── CP4 BUY THRESHOLDS (900s) ─────────────────────────────────────────────────
CP4_MIN_BUY_VOLUME_PCT   = 0.68  # ≥68% del volumen debe ser compras
CP4_MIN_ACCELERATION     = 1.25  # velocidad fast/slow ≥ 1.25×
CP4_MAX_RECENT_SELLS_PCT = 0.35  # ventas recientes < 35%
CP4_MIN_SCORE            = 60    # score mínimo para emitir señal BUY

# ── CP5 GEM DETECTION ─────────────────────────────────────────────────────────
# Top-of-top: tokens con aceleración excepcional → etiqueta GEM
CP5_MIN_BUY_VOLUME_PCT   = 0.75  # ≥75% del volumen en compras
CP5_MIN_ACCELERATION     = 1.40  # aceleración muy por encima del baseline
CP5_MAX_RECENT_SELLS_PCT = 0.25  # presión vendedora muy baja (< 25%)
CP5_MIN_TRADE_SPEED      = 0.25  # ≥0.25 trades/seg en ventana rápida
CP5_MIN_SCORE            = 80    # score mínimo para etiqueta GEM
CP5_MIN_VOL_GROWTH_PCT   = 0.20  # volumen últimos 2min > avg período × 1.20

# ── GEM RETENTION / TRACKING ──────────────────────────────────────────────────
GEM_OBSERVE_SEC = 900            # 15 min de seguimiento tras clasificación GEM

# ── HOTLIST ───────────────────────────────────────────────────────────────────
# Tokens que superan estos umbrales entran a observación extendida
HOTLIST_MIN_BUY_RATIO  = 0.80   # ≥80% buys sobre total trades
HOTLIST_MIN_VOLUME_SOL = 10.0   # ≥10 SOL de volumen total
HOTLIST_MIN_TRADES     = 100    # ≥200 trades
HOTLIST_OBSERVE_SEC    = 600    # 10 min de observación
# Confirmación: necesita 2 de 3 señales al final de la observación
HOTLIST_MAX_SELLS_PCT  = 0.35   # señal 1: recent_sells_pct < 0.35
HOTLIST_MIN_SPEED      = 0.20   # señal 2: trade_speed > 0.20 trades/seg
HOTLIST_VOL_GROWTH_PCT = 0.10   # señal 3: vol últimos 2min > avg período × (1 + este valor)