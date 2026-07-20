# analytics/token_enrichment.py
# Python 3.8-safe

from typing import Dict, Any
from analytics.holder_analysis import get_holder_distribution


def enrich_token_risk_data(token: Dict[str, Any]) -> Dict[str, Any]:
    """
    Construye el payload que después consume anti_rug_check().

    Por ahora:
    - usa lo que ya existe en token/raw
    - deja placeholders para métricas que todavía no estamos trayendo
    - centraliza el armado del payload para no mezclar fetch + validación

    Más adelante este módulo puede:
    - consultar liquidez real
    - holders/top holders
    - dev holdings
    """

    raw = token.get("raw", {}) or {}

    market_cap_sol = float(raw.get("marketCapSol", 0) or 0)
    liquidity_sol  = float(raw.get("vSolInBondingCurve", 0) or 0)

    holder_data = get_holder_distribution(token)

    payload = {
        "age_sec": float(token.get("pair_age", 0) or 0),
        "volSOL":  float(token.get("volume", 0) or 0),
        "buys":    int(token.get("buys", 0) or 0),
        "sells":   int(token.get("sells", 0) or 0),

        # Delegado a holder_analysis — placeholder por ahora,
        # listo para enriquecimiento real sin tocar este archivo.
        "dev_holdings": float(holder_data.get("dev_holdings", 0) or 0),
        "top_holder":   float(holder_data.get("largest_holder", 0) or 0),
        "liquiditySol": liquidity_sol,

        "marketCapSol": market_cap_sol,
    }

    return payload