import asyncio
import json
from typing import Any, Dict, Optional

import websockets

PUMPPORTAL_WS   = "wss://pumpportal.fun/api/data"
RECONNECT_DELAY = 3    # segundos entre intentos
MAX_RECONNECT   = 20   # intentos máximos antes de resetear contador


class PumpPortalScanner:
    def __init__(self) -> None:
        self.ws = None
        self.subscribed_tokens = set()
        self._reconnect_attempts = 0

    def _is_ws_usable(self) -> bool:
        """
        Comprueba si el websocket está disponible sin usar .closed,
        que no existe en todas las versiones de la librería.
        """
        if self.ws is None:
            return False
        # Soporte defensivo: algunos clientes exponen .closed, otros no
        closed = getattr(self.ws, "closed", None)
        if closed is True:
            return False
        return True

    async def connect(self) -> None:
        # Intentar cerrar limpio si hay una conexión previa
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

        while True:
            try:
                self.ws = await websockets.connect(
                    PUMPPORTAL_WS,
                    ping_interval=20,
                    ping_timeout=30,
                    close_timeout=10,
                )
                await self.ws.send(json.dumps({"method": "subscribeNewToken"}))
                self._reconnect_attempts = 0
                print("Connected to PumpPortal new token stream")

                # Re-subscribir tokens activos tras reconexión
                if self.subscribed_tokens:
                    print("Re-subscribing {} active tokens...".format(
                        len(self.subscribed_tokens)))
                    for token_address in list(self.subscribed_tokens):
                        try:
                            await self.ws.send(json.dumps({
                                "method": "subscribeTokenTrade",
                                "keys":   [token_address],
                            }))
                        except Exception:
                            pass
                return

            except Exception as e:
                self._reconnect_attempts += 1
                wait = min(RECONNECT_DELAY * self._reconnect_attempts, 60)
                print("PumpPortal connect error (attempt {}): {} — retry in {}s".format(
                    self._reconnect_attempts, e, wait))
                if self._reconnect_attempts >= MAX_RECONNECT:
                    print("Max reconnect attempts reached. Resetting counter.")
                    self._reconnect_attempts = 0
                self.ws = None
                await asyncio.sleep(wait)

    async def _ensure_connected(self) -> None:
        """Conecta si no hay ws usable."""
        if not self._is_ws_usable():
            print("WebSocket not usable — connecting...")
            await self.connect()

    async def subscribe_token_trades(self, token_address: str) -> None:
        await self._ensure_connected()

        if token_address in self.subscribed_tokens:
            return

        try:
            await self.ws.send(json.dumps({
                "method": "subscribeTokenTrade",
                "keys":   [token_address],
            }))
            self.subscribed_tokens.add(token_address)
            print("Subscribed trades for {}".format(token_address))
        except Exception as e:
            print("Subscribe error for {}: {}".format(token_address, e))
            self.ws = None   # marcar como inusable para forzar reconexión

    async def unsubscribe_token_trades(self, token_address: str) -> None:
        self.subscribed_tokens.discard(token_address)

        if not self._is_ws_usable():
            return

        try:
            await self.ws.send(json.dumps({
                "method": "unsubscribeTokenTrade",
                "keys":   [token_address],
            }))
            print("Unsubscribed trades for {}".format(token_address))
        except Exception:
            pass   # si falla el unsubscribe no es crítico

    def _normalize_new_token(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mint = data.get("mint")
        if not mint:
            return None

        now = asyncio.get_event_loop().time()

        return {
            "type":           "new_token",
            "address":        mint,
            "name":           data.get("name", ""),
            "symbol":         data.get("symbol", ""),
            "creator":        data.get("traderPublicKey", ""),
            "pair_age":       0,
            "volume":         float(data.get("solAmount", 0) or 0),
            "liquidity":      0.0,
            "trades":         1,
            "buys":           1 if float(data.get("solAmount", 0) or 0) > 0 else 0,
            "sells":          0,
            "buy_volume":     float(data.get("solAmount", 0) or 0),
            "sell_volume":    0.0,
            "price_change":   0.0,
            "dev_wallet":     0.0,
            "largest_holder": 0.0,
            "top10":          0.0,
            "created_at_ts":  now,
            "last_trade_ts":  now,
            "raw":            data,
        }

    def _normalize_trade(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mint = data.get("mint")
        if not mint:
            return None

        return {
            "type":           "trade",
            "address":        mint,
            "trade_type":     (data.get("txType") or "").lower(),
            "sol_amount":     float(data.get("solAmount", 0) or 0),
            "token_amount":   float(data.get("tokenAmount", 0) or 0),
            "trader":         data.get("traderPublicKey", ""),
            "market_cap_sol": float(data.get("marketCapSol", 0) or 0),
            "raw":            data,
        }

    async def recv_event(self) -> Optional[Dict[str, Any]]:
        await self._ensure_connected()

        try:
            message = await self.ws.recv()

            data    = json.loads(message)
            tx_type = (data.get("txType") or "").lower()

            if "message" in data:
                return None

            if data.get("mint") and tx_type == "create":
                return self._normalize_new_token(data)

            if data.get("mint") and tx_type in {"buy", "sell"}:
                return self._normalize_trade(data)

            return None

        except Exception as e:
            print("recv_event error: {} — reconnecting...".format(e))
            self.ws = None
            await self.connect()
            return None