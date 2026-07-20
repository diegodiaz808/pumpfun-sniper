# strategy/anti_rug.py
# Python 3.8-safe

"""
Anti-rug system V4

Responsabilidad:
  Detectar señales de peligro del token, sin mezclar lógica temporal.
  La edad y expiración del token ya la controla main.py con checkpoints.

Este módulo NO decide si el token está "muy viejo" ni "muy nuevo".
Solo decide si el token parece peligroso para comprar.

Checks activos  →  razón devuelta
──────────────────────────────────────────────────────────────
  buys / (buys+sells) < MIN_BUY_RATIO          → weak_buy_pressure
  buy_volume_pct      < MIN_BUY_VOLUME_PCT      → weak_buy_volume
  dev_holdings        > MAX_DEV_HOLDINGS        → dev_too_large
  top_holder          > MAX_TOP_HOLDER          → top_holder_too_large
  market_cap          > MAX_MARKETCAP_SOL       → marketcap_too_high
  liquidity           < MIN_LIQUIDITY_SOL       → low_liquidity
  quote.priceImpactPct> MAX_PRICE_IMPACT        → high_price_impact
  trade_speed         > MAX_TRADE_SPEED         → trade_speed_too_high
  recent_sells_pct    > MAX_RECENT_SELLS        → sell_spike_detected

Campos enriquecidos aceptados desde main.py / enrich_token_risk_data():
  buy_volume_pct, trade_speed, recent_sells_pct,
  buy_volume, sell_volume  (fallback para calcular buy_volume_pct)

Razones de tiempo EXCLUIDAS deliberadamente:
  too_old, too_new  →  responsabilidad exclusiva de main.py (checkpoints).
"""

# ── Umbrales base ─────────────────────────────────────────────────────────────

MIN_BUY_RATIO        = 0.55   # mínimo 55% de trades son buys
MIN_BUY_VOLUME_PCT   = 0.55   # mínimo 55% del volumen debe ser buy-side (si existe dato)
MAX_DEV_HOLDINGS     = 0.10   # dev no puede tener >10%
MAX_TOP_HOLDER       = 0.15   # top holder no puede tener >15%
MAX_PRICE_IMPACT     = 0.15   # 15% máximo
MAX_TRADE_SPEED      = 5.0    # trades/seg muy alto = sospecha de wash trading
MAX_RECENT_SELLS     = 0.60   # >60% sells recientes = dump activo
MIN_LIQUIDITY_SOL    = 5.0    # liquidez mínima si el dato está presente
MAX_MARKETCAP_SOL    = 800    # evitar tokens ya demasiado inflados


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def anti_rug_check(token_data, market_data=None, quote=None):
    """
    Evalúa si un token parece suficientemente seguro para comprar.

    Args:
        token_data (dict):
            Dict enriquecido por enrich_token_risk_data() + métricas del watchlist.
            Campos reconocidos (todos opcionales salvo que se indique):
              buys, sells                  – conteo de trades por lado
              buy_volume_pct               – fracción del volumen en compras [0.0–1.0]
                                             Si no está presente se calcula desde:
              buy_volume, sell_volume      – volúmenes crudos por lado (SOL)
              dev_holdings / dev_wallet    – fracción del supply en manos del dev
              top_holder / largest_holder  – fracción del supply del mayor holder
              marketCapSol / market_cap    – market cap en SOL
              liquiditySol / liquidity     – liquidez del pool en SOL
              trade_speed                  – trades/seg (ventana reciente)
              recent_sells_pct             – fracción de sells en ventana reciente

        market_data:
            No usado; mantenido por compatibilidad con llamadas existentes.

        quote (dict | None):
            Respuesta de Jupiter (opcional). Si viene, se extrae priceImpactPct.

    Returns:
        tuple[bool, list[str]]:
            ok      – True si no se detectó ningún riesgo.
            reasons – Lista de strings con los checks fallidos (vacía si ok=True).
                      Valores posibles: weak_buy_pressure, weak_buy_volume,
                      dev_too_large, top_holder_too_large, marketcap_too_high,
                      low_liquidity, high_price_impact, trade_speed_too_high,
                      sell_spike_detected.
                      NUNCA contiene razones de tiempo (too_old, too_new, etc.).
    """
    reasons = []

    # ── Trades side ratio ─────────────────────────────────────────────────────
    # Use _safe_float before int() so None / non-numeric values never raise.
    buys  = int(_safe_float(token_data.get("buys",  0), default=0.0))
    sells = int(_safe_float(token_data.get("sells", 0), default=0.0))
    total_trades = buys + sells

    if total_trades > 0:
        buy_ratio = buys / float(total_trades)
        if buy_ratio < MIN_BUY_RATIO:
            reasons.append("weak_buy_pressure")

    # ── Buy volume ratio (más importante que el simple count) ────────────────
    buy_volume_pct = token_data.get("buy_volume_pct", None)

    if buy_volume_pct is None:
        buy_volume = _safe_float(token_data.get("buy_volume", None), default=-1.0)
        sell_volume = _safe_float(token_data.get("sell_volume", None), default=-1.0)

        if buy_volume >= 0.0 and sell_volume >= 0.0:
            total_volume = buy_volume + sell_volume
            if total_volume > 0:
                buy_volume_pct = buy_volume / float(total_volume)

    if buy_volume_pct is not None:
        buy_volume_pct = _safe_float(buy_volume_pct, default=0.0)
        if buy_volume_pct < MIN_BUY_VOLUME_PCT:
            reasons.append("weak_buy_volume")

    # ── Dev holdings ──────────────────────────────────────────────────────────
    dev_holdings = token_data.get("dev_holdings", None)
    if dev_holdings is None:
        dev_holdings = token_data.get("dev_wallet", None)

    if dev_holdings is not None:
        dev_holdings = _safe_float(dev_holdings, default=0.0)
        if dev_holdings > MAX_DEV_HOLDINGS:
            reasons.append("dev_too_large")

    # ── Top holder concentration ──────────────────────────────────────────────
    top_holder = token_data.get("top_holder", None)
    if top_holder is None:
        top_holder = token_data.get("largest_holder", None)

    if top_holder is not None:
        top_holder = _safe_float(top_holder, default=0.0)
        if top_holder > MAX_TOP_HOLDER:
            reasons.append("top_holder_too_large")

    # ── Market cap ────────────────────────────────────────────────────────────
    market_cap = token_data.get("marketCapSol", None)
    if market_cap is None:
        market_cap = token_data.get("market_cap", None)

    if market_cap is not None:
        market_cap = _safe_float(market_cap, default=0.0)
        if market_cap > MAX_MARKETCAP_SOL:
            reasons.append("marketcap_too_high")

    # ── Liquidity (solo si el dato existe) ────────────────────────────────────
    liquidity = token_data.get("liquiditySol", None)
    if liquidity is None:
        liquidity = token_data.get("liquidity", None)

    if liquidity is not None:
        liquidity = _safe_float(liquidity, default=0.0)
        if liquidity > 0.0 and liquidity < MIN_LIQUIDITY_SOL:
            reasons.append("low_liquidity")

    # ── Price impact (quote opcional) ─────────────────────────────────────────
    if quote:
        try:
            impact = _safe_float(quote.get("priceImpactPct", 0), default=0.0)
            if impact > MAX_PRICE_IMPACT:
                reasons.append("high_price_impact")
        except Exception:
            pass

    # ── Trade speed / wash trading suspicion ──────────────────────────────────
    trade_speed = _safe_float(token_data.get("trade_speed", 0.0), default=0.0)
    if trade_speed > MAX_TRADE_SPEED:
        reasons.append("trade_speed_too_high")

    # ── Sell spike / dump activo ──────────────────────────────────────────────
    recent_sells_pct = _safe_float(token_data.get("recent_sells_pct", 0.0), default=0.0)
    if recent_sells_pct > MAX_RECENT_SELLS:
        reasons.append("sell_spike_detected")

    ok = len(reasons) == 0
    return ok, reasons