# execution/jupiter_trader.py
# Python 3.8-safe

import os
import math
import base64
import requests
import base58

from dotenv import load_dotenv

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned

load_dotenv()

JUPITER_BASE_URL = os.getenv("JUPITER_BASE_URL", "https://lite-api.jup.ag")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
TRADE_SIZE_SOL = float(os.getenv("TRADE_SIZE_SOL", "0.01"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000


def _sol_to_lamports(sol_amount):
    return int(math.floor(sol_amount * LAMPORTS_PER_SOL))


def _load_keypair_from_base58(secret_b58):
    if not secret_b58:
        raise ValueError("PRIVATE_KEY vacío en .env")

    secret_bytes = base58.b58decode(secret_b58)

    if len(secret_bytes) != 64:
        raise ValueError(
            "PRIVATE_KEY base58 debe decodificar a 64 bytes, llegó {}".format(len(secret_bytes))
        )

    return Keypair.from_bytes(secret_bytes)


def _get_quote(output_mint, sol_amount, slippage_bps=100):
    amount_lamports = _sol_to_lamports(sol_amount)

    url = "{}/swap/v1/quote".format(JUPITER_BASE_URL.rstrip("/"))
    params = {
        "inputMint": SOL_MINT,
        "outputMint": output_mint,
        "amount": amount_lamports,
        "slippageBps": slippage_bps,
        "restrictIntermediateTokens": "true",
    }

    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _get_sell_quote(input_mint, raw_amount, slippage_bps=100):
    """
    Cotiza venta: token -> SOL
    """
    url = "{}/swap/v1/quote".format(JUPITER_BASE_URL.rstrip("/"))
    params = {
        "inputMint": input_mint,
        "outputMint": SOL_MINT,
        "amount": int(raw_amount),
        "slippageBps": slippage_bps,
        "restrictIntermediateTokens": "true",
    }

    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _build_swap_tx(quote, user_public_key):
    url = "{}/swap/v1/swap".format(JUPITER_BASE_URL.rstrip("/"))
    payload = {
        "quoteResponse": quote,
        "userPublicKey": user_public_key,
        "wrapAndUnwrapSol": True,
        "useSharedAccounts": False,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": "auto",
    }

    resp = requests.post(url, json=payload, timeout=30)

    if not resp.ok:
        print("=" * 80)
        print("[JUPITER SWAP BUILD ERROR]")
        print("url: {}".format(url))
        print("status_code: {}".format(resp.status_code))
        print("user_public_key: {}".format(user_public_key))
        try:
            print("response_json: {}".format(resp.json()))
        except Exception:
            print("response_text: {}".format(resp.text))
        print("payload_keys: {}".format(list(payload.keys())))
        print("=" * 80)
        resp.raise_for_status()

    return resp.json()


def _sign_versioned_tx(swap_tx_b64, keypair):
    raw_tx = base64.b64decode(swap_tx_b64)
    tx = VersionedTransaction.from_bytes(raw_tx)

    message_bytes = to_bytes_versioned(tx.message)
    signature = keypair.sign_message(message_bytes)

    signed_tx = VersionedTransaction.populate(tx.message, [signature])
    return bytes(signed_tx)


def _send_raw_transaction(signed_tx_bytes):
    if not SOLANA_RPC_URL:
        raise ValueError("SOLANA_RPC_URL vacío en .env")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            base64.b64encode(signed_tx_bytes).decode("utf-8"),
            {
                "encoding": "base64",
                "skipPreflight": False,
                "preflightCommitment": "processed",
                "maxRetries": 3,
            },
        ],
    }

    resp = requests.post(SOLANA_RPC_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError("RPC sendTransaction error: {}".format(data["error"]))

    return data.get("result")


def buy_token(token_mint, trade_size_sol=None):
    try:
        size  = trade_size_sol if trade_size_sol is not None else TRADE_SIZE_SOL
        quote = _get_quote(token_mint, size, slippage_bps=100)

        in_amount = quote.get("inAmount")
        out_amount = quote.get("outAmount")
        price_impact_pct = quote.get("priceImpactPct")
        route_plan = quote.get("routePlan", [])

        print("=" * 80)
        print("[JUPITER BUY QUOTE]")
        print("token mint: {}".format(token_mint))
        print("input: {} SOL".format(size))
        print("inAmount(lamports): {}".format(in_amount))
        print("outAmount(raw): {}".format(out_amount))
        print("priceImpactPct: {}".format(price_impact_pct))
        print("route hops: {}".format(len(route_plan)))
        print("DRY_RUN: {}".format(DRY_RUN))

        if DRY_RUN:
            print("[DRY RUN] No se envía transacción.")
            print("=" * 80)
            return {
                "ok": True,
                "mode": "dry_run",
                "token_mint": token_mint,
                "quote": quote,
                "out_amount_raw": out_amount,
            }

        keypair = _load_keypair_from_base58(PRIVATE_KEY)
        user_public_key = str(keypair.pubkey())

        swap_data = _build_swap_tx(quote, user_public_key)
        swap_tx_b64 = swap_data.get("swapTransaction")

        if not swap_tx_b64:
            raise RuntimeError("Jupiter no devolvió swapTransaction")

        signed_tx_bytes = _sign_versioned_tx(swap_tx_b64, keypair)
        tx_sig = _send_raw_transaction(signed_tx_bytes)

        print("[JUPITER BUY SENT]")
        print("token mint: {}".format(token_mint))
        print("tx_sig: {}".format(tx_sig))
        print("=" * 80)

        return {
            "ok": True,
            "mode": "live",
            "token_mint": token_mint,
            "tx_sig": tx_sig,
            "quote": quote,
            "out_amount_raw": out_amount,
        }

    except Exception as e:
        print("=" * 80)
        print("[JUPITER BUY ERROR]")
        print("token: {}".format(token_mint))
        print("error: {}".format(e))
        print("=" * 80)
        return {
            "ok": False,
            "error": str(e),
            "token_mint": token_mint,
        }


def sell_token(token_mint, raw_amount=None, sell_pct=1.0):
    """
    Vende token -> SOL.
    raw_amount: cantidad raw total del token
    sell_pct: 1.0 = 100%, 0.85 = 85%, etc.
    """
    try:
        if raw_amount is None:
            raise ValueError("sell_token requiere raw_amount")

        amount_to_sell = int(int(raw_amount) * float(sell_pct))
        if amount_to_sell <= 0:
            raise ValueError("amount_to_sell <= 0")

        quote = _get_sell_quote(token_mint, amount_to_sell, slippage_bps=100)

        in_amount = quote.get("inAmount")
        out_amount = quote.get("outAmount")
        price_impact_pct = quote.get("priceImpactPct")
        route_plan = quote.get("routePlan", [])

        print("=" * 80)
        print("[JUPITER SELL QUOTE]")
        print("token mint: {}".format(token_mint))
        print("sell_pct: {}".format(sell_pct))
        print("inAmount(raw token): {}".format(in_amount))
        print("outAmount(lamports SOL): {}".format(out_amount))
        print("priceImpactPct: {}".format(price_impact_pct))
        print("route hops: {}".format(len(route_plan)))
        print("DRY_RUN: {}".format(DRY_RUN))

        if DRY_RUN:
            print("[DRY RUN] No se envía SELL.")
            print("=" * 80)
            return {
                "ok": True,
                "mode": "dry_run",
                "token_mint": token_mint,
                "quote": quote,
                "sold_raw": amount_to_sell,
                "out_amount_raw": out_amount,
            }

        keypair = _load_keypair_from_base58(PRIVATE_KEY)
        user_public_key = str(keypair.pubkey())

        swap_data = _build_swap_tx(quote, user_public_key)
        swap_tx_b64 = swap_data.get("swapTransaction")

        if not swap_tx_b64:
            raise RuntimeError("Jupiter no devolvió swapTransaction en sell")

        signed_tx_bytes = _sign_versioned_tx(swap_tx_b64, keypair)
        tx_sig = _send_raw_transaction(signed_tx_bytes)

        print("[JUPITER SELL SENT]")
        print("token mint: {}".format(token_mint))
        print("tx_sig: {}".format(tx_sig))
        print("=" * 80)

        return {
            "ok": True,
            "mode": "live",
            "token_mint": token_mint,
            "tx_sig": tx_sig,
            "quote": quote,
            "sold_raw": amount_to_sell,
            "out_amount_raw": out_amount,
        }

    except Exception as e:
        print("=" * 80)
        print("[JUPITER SELL ERROR]")
        print("token: {}".format(token_mint))
        print("error: {}".format(e))
        print("=" * 80)
        return {
            "ok": False,
            "error": str(e),
            "token_mint": token_mint,
        }