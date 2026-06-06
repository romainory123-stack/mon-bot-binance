"""
=============================================================
  BOT DE TRADING BINANCE — Stratégie Grid + RSI
  Auteur : généré par Claude
  Prérequis : pip install python-binance pandas ta
=============================================================
"""

import time
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
import pandas as pd
import ta

# ─────────────────────────────────────────
#  CONFIGURATION — Modifie ces valeurs
# ─────────────────────────────────────────

import os
API_KEY    = os.environ.get("pdu4MbB6ibVgSyRGy32PVEe5SybmcAINnkTvDJErdUxyyKrjBCgkCutpgaTk30Bt")
API_SECRET = os.environ.get("SEH1Yd4YlaNgY8vPNCLjjX4PKfhl756I1WrVjEZic4q5RMzYL77zb0Hi1iJdvsPK")

SYMBOL          = "BTCUSDT"    # Paire tradée (ex: ETHUSDT, BNBUSDT)
TRADE_AMOUNT    = 10.0         # Montant en USDT par ordre (adapte à ton capital)
GRID_LEVELS     = 5            # Nombre de niveaux du grid
GRID_SPACING    = 0.5          # Espacement entre niveaux en % (0.5 = 0.5%)

# Filtres RSI — le bot n'achète que si RSI < RSI_BUY
RSI_PERIOD      = 14
RSI_BUY         = 40           # RSI oversold → signal d'achat
RSI_SELL        = 60           # RSI overbought → signal de vente

CHECK_INTERVAL  = 60           # Intervalle entre chaque vérification (secondes)

# ─────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────
#  FONCTIONS UTILITAIRES
# ─────────────────────────────────────────

def get_client():
    """Connexion à l'API Binance."""
    client = Client(API_KEY, API_SECRET)
    log.info("✅ Connexion à Binance réussie.")
    return client


def get_current_price(client, symbol: str) -> float:
    """Retourne le prix actuel d'une paire."""
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])


def get_rsi(client, symbol: str, period: int = 14, interval: str = "1h") -> float:
    """Calcule le RSI sur les dernières bougies."""
    klines = client.get_klines(symbol=symbol, interval=interval, limit=period + 10)
    closes = pd.Series([float(k[4]) for k in klines])
    rsi = ta.momentum.RSIIndicator(close=closes, window=period).rsi()
    return round(rsi.iloc[-1], 2)


def get_balance(client, asset: str) -> float:
    """Retourne le solde disponible d'un asset."""
    balance = client.get_asset_balance(asset=asset)
    return float(balance["free"]) if balance else 0.0


def place_order(client, symbol: str, side: str, amount_usdt: float, price: float):
    """
    Passe un ordre limit.
    side : 'BUY' ou 'SELL'
    """
    try:
        info = client.get_symbol_info(symbol)
        lot_filter = next(f for f in info["filters"] if f["filterType"] == "LOT_SIZE")
        step_size = float(lot_filter["stepSize"])
        quantity = round(amount_usdt / price, 6)
        # Arrondi au step_size
        quantity = round(quantity - (quantity % step_size), 8)

        order = client.create_order(
            symbol=symbol,
            side=side,
            type=Client.ORDER_TYPE_LIMIT,
            timeInForce=Client.TIME_IN_FORCE_GTC,
            quantity=quantity,
            price=str(round(price, 2))
        )
        log.info(f"📋 Ordre {side} placé : {quantity} {symbol} @ {price} USDT")
        return order
    except BinanceAPIException as e:
        log.error(f"❌ Erreur ordre {side} : {e}")
        return None


def build_grid(current_price: float, levels: int, spacing_pct: float):
    """
    Génère les niveaux du grid autour du prix actuel.
    Retourne (buy_levels, sell_levels).
    """
    step = current_price * (spacing_pct / 100)
    buy_levels  = [round(current_price - step * (i + 1), 2) for i in range(levels)]
    sell_levels = [round(current_price + step * (i + 1), 2) for i in range(levels)]
    return buy_levels, sell_levels


# ─────────────────────────────────────────
#  LOGIQUE PRINCIPALE
# ─────────────────────────────────────────

def run_bot():
    client = get_client()
    active_orders = {}  # price → order_id

    log.info(f"🚀 Démarrage du bot sur {SYMBOL}")
    log.info(f"   Grid : {GRID_LEVELS} niveaux, espacement {GRID_SPACING}%")
    log.info(f"   RSI  : achat < {RSI_BUY} | vente > {RSI_SELL}")

    while True:
        try:
            price = get_current_price(client, SYMBOL)
            rsi   = get_rsi(client, SYMBOL, RSI_PERIOD)
            usdt_balance = get_balance(client, "USDT")

            log.info(f"💰 Prix : {price} USDT | RSI({RSI_PERIOD}) : {rsi} | Balance USDT : {usdt_balance:.2f}")

            buy_levels, sell_levels = build_grid(price, GRID_LEVELS, GRID_SPACING)

            # ── Placer des ordres BUY si RSI oversold ──
            if rsi < RSI_BUY:
                for level in buy_levels:
                    if level not in active_orders and usdt_balance >= TRADE_AMOUNT:
                        order = place_order(client, SYMBOL, "BUY", TRADE_AMOUNT, level)
                        if order:
                            active_orders[level] = order["orderId"]
                            usdt_balance -= TRADE_AMOUNT

            # ── Placer des ordres SELL si RSI overbought ──
            elif rsi > RSI_SELL:
                asset = SYMBOL.replace("USDT", "")
                asset_balance = get_balance(client, asset)
                sell_value = asset_balance * price

                if sell_value >= TRADE_AMOUNT:
                    for level in sell_levels:
                        if level not in active_orders:
                            order = place_order(client, SYMBOL, "SELL", TRADE_AMOUNT, level)
                            if order:
                                active_orders[level] = order["orderId"]

            else:
                log.info("⏸️  RSI neutre — pas d'action.")

            # ── Nettoyage des ordres remplis ──
            filled = []
            for price_level, order_id in active_orders.items():
                try:
                    status = client.get_order(symbol=SYMBOL, orderId=order_id)
                    if status["status"] == "FILLED":
                        log.info(f"✅ Ordre {order_id} rempli @ {price_level}")
                        filled.append(price_level)
                except BinanceAPIException:
                    filled.append(price_level)

            for p in filled:
                del active_orders[p]

        except BinanceAPIException as e:
            log.error(f"⚠️  Erreur Binance : {e}")
        except Exception as e:
            log.error(f"⚠️  Erreur inattendue : {e}")

        time.sleep(CHECK_INTERVAL)


# ─────────────────────────────────────────
#  POINT D'ENTRÉE
# ─────────────────────────────────────────

if __name__ == "__main__":
    run_bot()
