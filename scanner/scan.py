"""HH + HL trend scanner voor US stocks, crypto (Binance) en goud.

Draait elk uur (GitHub Actions) en stuurt een Telegram-bericht zodra een
ticker een nieuwe uptrend maakt: Higher High + Higher Low op 4h of 1D.
Meldingen staan gesorteerd op dagvolume in dollar (hoogste eerst).

Gebruik:
    python scanner/scan.py                 # normale run
    python scanner/scan.py --dry-run       # alleen printen, niets versturen/opslaan
    python scanner/scan.py --test-telegram # stuur een testbericht
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

# ---------------------------------------------------------------- instellingen

# Aantal candles links en rechts van een top/bodem om hem te bevestigen.
PIVOT_LEN = {"4h": 2, "1d": 2}

# Filter voor US stocks.
MIN_PRICE = 5.0
MIN_AVG_VOLUME = 500_000  # gemiddeld dagvolume over 20 dagen

# Een signaal telt als het op een van de laatste N gesloten candles ontstond
# (vangt een gemiste of vertraagde run op; dubbele meldingen worden voorkomen).
RECENT_BARS = 3

GOLD = {"yahoo": "GC=F", "name": "GOLD", "tv": "COMEX:GC1!"}

STATE_FILE = Path(__file__).with_name("state.json")
ET = ZoneInfo("America/New_York")
BINANCE = "https://data-api.binance.vision/api/v3"  # publieke data, geen geo-block
NASDAQ_LIST = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

STABLECOINS = {
    "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "PAX", "EUR", "EURI", "AEUR",
    "GBP", "USDE", "XUSD", "BFUSD", "USD1", "RLUSD", "PYUSD", "UST", "USDS", "U",
}
TV_EXCHANGE = {"Q": "NASDAQ", "N": "NYSE", "A": "AMEX", "P": "AMEX", "Z": "CBOE"}
JUNK_NAME = re.compile(
    r"warrant|\bunits?\b|\brights?\b|preferred|\bnotes?\b|debenture|%", re.I
)

http = requests.Session()
http.headers["User-Agent"] = "Mozilla/5.0 (hh-hl-scanner)"


def log(*args):
    print(*args, flush=True)


# ------------------------------------------------------------------ trendlogica


def find_signal(df: pd.DataFrame, length: int) -> dict | None:
    """Zoekt het moment waarop de trend omslaat naar HH + HL.

    Zelfde logica als de Pine Script-indicator: een top (pivot high) is
    bevestigd als er `length` lagere candles links en rechts van liggen.
    Uptrend = laatste top > vorige top EN laatste bodem > vorige bodem.
    Geeft het signaal terug als het recent is en de uptrend nog loopt.
    """
    n = len(df)
    if n < 4 * length + 2:
        return None
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    last_h = prev_h = last_l = prev_l = math.nan
    was_up = False
    signal = None

    for j in range(2 * length, n):  # j = candle waarop de pivot bevestigd wordt
        i = j - length
        if h[i] > h[i - length:i].max() and h[i] >= h[i + 1:j + 1].max():
            prev_h, last_h = last_h, h[i]
        if l[i] < l[i - length:i].min() and l[i] <= l[i + 1:j + 1].min():
            prev_l, last_l = last_l, l[i]
        up = last_h > prev_h and last_l > prev_l  # nan-vergelijkingen zijn False
        if up and not was_up:
            signal = {"bar": j, "hh": last_h, "prev_h": prev_h,
                      "hl": last_l, "prev_l": prev_l}
        was_up = up

    if signal and was_up and signal["bar"] >= n - RECENT_BARS:
        signal["time"] = df.index[signal["bar"]]
        signal["close"] = float(df["close"].iloc[-1])
        return signal
    return None


# ----------------------------------------------------------------------- crypto


def binance_symbols() -> list[str]:
    info = http.get(f"{BINANCE}/exchangeInfo", timeout=30).json()
    out = []
    for s in info["symbols"]:
        base = s["baseAsset"]
        if (s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
                and s.get("isSpotTradingAllowed", True)
                and base not in STABLECOINS
                and not base.endswith(("UP", "DOWN", "BULL", "BEAR"))):
            out.append(s["symbol"])
    return sorted(out)


def binance_klines(symbol: str, interval: str, now_ms: int) -> pd.DataFrame | None:
    for attempt in range(3):
        try:
            r = http.get(f"{BINANCE}/klines", timeout=20, params={
                "symbol": symbol, "interval": interval, "limit": 300})
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            rows = [k for k in r.json() if k[6] < now_ms]  # alleen gesloten candles
            if not rows:
                return None
            df = pd.DataFrame(
                [[float(x) for x in k[1:6]] for k in rows],
                columns=["open", "high", "low", "close", "volume"],
                index=pd.to_datetime([k[0] for k in rows], unit="ms", utc=True),
            )
            return df
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    return None


def binance_volumes() -> dict[str, float]:
    """24h-volume in USDT per paar."""
    data = http.get(f"{BINANCE}/ticker/24hr", timeout=30).json()
    return {t["symbol"]: float(t["quoteVolume"]) for t in data}


def scan_crypto() -> list[dict]:
    symbols = binance_symbols()
    volumes = binance_volumes()
    log(f"Crypto: {len(symbols)} USDT-paren")
    now_ms = int(time.time() * 1000)
    jobs = [(s, tf) for s in symbols for tf in PIVOT_LEN]

    def work(job):
        sym, tf = job
        df = binance_klines(sym, tf, now_ms)
        sig = find_signal(df, PIVOT_LEN[tf]) if df is not None else None
        if sig:
            sig.update(name=sym, tf=tf, tv=f"BINANCE:{sym}", vol=volumes.get(sym, 0.0))
        return sig

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [s for s in pool.map(work, jobs) if s]


# ------------------------------------------------------------------ yahoo data


def yahoo_download(tickers: list[str], period: str, interval: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    frames: dict[str, pd.DataFrame] = {}
    for start in range(0, len(tickers), 200):
        chunk = tickers[start:start + 200]
        for attempt in range(3):
            try:
                data = yf.download(
                    chunk, period=period, interval=interval, group_by="ticker",
                    auto_adjust=False, prepost=False, threads=True, progress=False,
                )
                break
            except Exception as e:  # rate limit of netwerkfout
                log(f"  yahoo fout ({e}), opnieuw proberen")
                time.sleep(20 * (attempt + 1))
        else:
            continue
        if data is None or data.empty:
            continue
        for t in chunk:
            try:
                df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
            except KeyError:
                continue
            df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
            df = df.dropna(subset=["open", "high", "low", "close"])
            if len(df):
                if df.index.tz is not None:  # alles in naive New York-tijd
                    df.index = df.index.tz_convert(ET).tz_localize(None)
                frames[t] = df
        time.sleep(1)
    return frames


def closed_only(df: pd.DataFrame, ends: pd.Series, now: datetime) -> pd.DataFrame:
    return df[ends.to_numpy() <= pd.Timestamp(now)]


def aggregate(df: pd.DataFrame, starts: pd.Series) -> pd.DataFrame:
    return df.groupby(starts.to_numpy()).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})


def stock_1h(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    df = df[df.index.minute == 30]  # Yahoo-candles starten op :30 (9:30 ... 15:30)
    ends = pd.Series(df.index + pd.Timedelta(hours=1), index=df.index)
    ends = ends.where(ends.dt.hour < 16, ends.dt.normalize() + pd.Timedelta(hours=16))
    return closed_only(df, ends, now)


def stock_4h(df1h: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """4h zoals TradingView: 9:30-13:30 en 13:30-16:00 (New York-tijd)."""
    if df1h.empty:
        return df1h
    day = df1h.index.normalize()
    second = (df1h.index.hour * 60 + df1h.index.minute) >= 13 * 60 + 30
    starts = pd.Series(day + pd.to_timedelta(
        [13.5 if s else 9.5 for s in second], unit="h"), index=df1h.index)
    df4 = aggregate(df1h, starts)
    ends = pd.Series([s + pd.Timedelta(hours=4) if s.hour == 9 else
                      s.normalize() + pd.Timedelta(hours=16) for s in df4.index], index=df4.index)
    return closed_only(df4, ends, now)


def stock_1d(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    ends = pd.Series(df.index.normalize() + pd.Timedelta(hours=16), index=df.index)
    return closed_only(df, ends, now)


def gold_1h(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    df = df[df.index.minute == 0]
    return closed_only(df, pd.Series(df.index + pd.Timedelta(hours=1), index=df.index), now)


def gold_4h(df1h: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """4h zoals TradingView COMEX: sessie start 18:00 ET -> 18, 22, 2, 6, 10, 14."""
    if df1h.empty:
        return df1h
    starts = pd.Series((df1h.index - pd.Timedelta(hours=2)).floor("4h")
                       + pd.Timedelta(hours=2), index=df1h.index)
    df4 = aggregate(df1h, starts)
    ends = pd.Series([s + pd.Timedelta(hours=3 if s.hour == 14 else 4)
                      for s in df4.index], index=df4.index)
    return closed_only(df4, ends, now)


def gold_1d(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    ends = pd.Series(df.index.normalize() + pd.Timedelta(hours=17), index=df.index)
    return closed_only(df, ends, now)


# ----------------------------------------------------------------------- stocks


def stock_candidates() -> dict[str, dict]:
    """Alle US-aandelen (NYSE, Nasdaq, AMEX, ...) zonder ETF's, warrants, etc."""
    text = http.get(NASDAQ_LIST, timeout=60).text
    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str).dropna(subset=["Symbol"])
    df = df[(df["Test Issue"] == "N") & (df["ETF"] == "N")]
    df = df[df["Symbol"].str.fullmatch(r"[A-Z]+(\.[A-Z])?")]
    df = df[~df["Security Name"].fillna("").str.contains(JUNK_NAME)]
    out = {}
    for _, row in df.iterrows():
        sym = row["Symbol"]
        ex = TV_EXCHANGE.get(row["Listing Exchange"])
        out[sym.replace(".", "-")] = {
            "name": sym, "tv": f"{ex}:{sym}" if ex else sym}
    return out


def scan_stocks_daily(state: dict, now: datetime) -> list[dict]:
    """Bouwt het universum (prijs/volume-filter) en zoekt daily-signalen."""
    candidates = stock_candidates()
    log(f"Stocks: {len(candidates)} kandidaten, daily data ophalen...")
    frames = yahoo_download(list(candidates), "1y", "1d")
    universe, signals = {}, []
    for t, df in frames.items():
        df = stock_1d(df, now)
        if len(df) < 20:
            continue
        if df["close"].iloc[-1] < MIN_PRICE or df["volume"].tail(20).mean() < MIN_AVG_VOLUME:
            continue
        universe[t] = {**candidates[t],
                       "vol": float((df["close"] * df["volume"]).tail(20).mean())}
        sig = find_signal(df, PIVOT_LEN["1d"])
        if sig:
            sig.update(tf="1d", **universe[t])
            signals.append(sig)
    log(f"Stocks: {len(universe)} na filter (prijs > ${MIN_PRICE:g}, volume > {MIN_AVG_VOLUME:,})")
    if universe:  # bij een mislukte download het oude universum houden
        state["universe"] = universe
    return signals


def scan_stocks_intraday(state: dict, now: datetime) -> list[dict]:
    universe = state.get("universe", {})
    log(f"Stocks intraday: {len(universe)} tickers")
    frames = yahoo_download(list(universe), "60d", "1h")
    signals = []
    for t, df in frames.items():
        sig = find_signal(stock_4h(stock_1h(df, now), now), PIVOT_LEN["4h"])
        if sig:
            sig.update(tf="4h", **universe[t])
            signals.append(sig)
    return signals


def scan_gold(now: datetime) -> list[dict]:
    signals = []
    h = yahoo_download([GOLD["yahoo"]], "60d", "1h").get(GOLD["yahoo"])
    d = yahoo_download([GOLD["yahoo"]], "1y", "1d").get(GOLD["yahoo"])
    sets, vol = [], 0.0
    if h is not None:
        sets.append(("4h", gold_4h(gold_1h(h, now), now)))
    if d is not None:
        d = gold_1d(d, now)
        sets.append(("1d", d))
        vol = float((d["close"] * d["volume"] * 100).tail(20).mean())  # 100 oz per contract
    for tf, data in sets:
        sig = find_signal(data, PIVOT_LEN[tf])
        if sig:
            sig.update(tf=tf, name=GOLD["name"], tv=GOLD["tv"], vol=vol)
            signals.append(sig)
    return signals


# --------------------------------------------------------------------- melding


def fmt(x: float) -> str:
    if x >= 1:
        return f"{x:,.2f}"
    return f"{x:.8f}".rstrip("0").rstrip(".")


def fmt_vol(x: float) -> str:
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= div:
            return f"${x / div:.1f}{unit}"
    return f"${x:.0f}"


def format_signal(s: dict) -> str:
    chart = f"https://www.tradingview.com/chart/?symbol={s['tv']}&interval=" + \
        {"4h": "240", "1d": "D"}[s["tf"]]
    symbol_page = f"https://www.tradingview.com/symbols/{s['tv'].replace(':', '-')}/"
    ticker = s["tv"].split(":")[-1]  # zoals je hem in TradingView intypt
    return (f"🔔 <code>{ticker}</code> HH + HL op <b>{s['tf']}</b> · Vol {fmt_vol(s.get('vol', 0))}/dag\n"
            f"HH {fmt(s['hh'])} (vorige {fmt(s['prev_h'])}) · "
            f"HL {fmt(s['hl'])} (vorige {fmt(s['prev_l'])})\n"
            f"Koers {fmt(s['close'])} · <a href=\"{chart}\">Chart</a> · "
            f"<a href=\"{symbol_page}\">App</a>")


def send_telegram(messages: list[str]) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("Geen TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ingesteld, alleen geprint.")
        return
    chunk = ""
    batches = []
    for m in messages:
        if len(chunk) + len(m) + 2 > 4000:
            batches.append(chunk)
            chunk = ""
        chunk += m + "\n\n"
    if chunk:
        batches.append(chunk)
    for text in batches:
        r = http.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, json={
            "chat_id": chat, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True})
        if not r.ok:
            log(f"Telegram fout: {r.status_code} {r.text}")
        time.sleep(1)


# ------------------------------------------------------------------------ main


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    if "--test-telegram" in sys.argv:
        send_telegram(["✅ HH/HL scanner: testbericht, Telegram werkt."])
        return 0

    state = load_state()
    state.setdefault("alerts", {})
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET).replace(tzinfo=None)  # naive New York-tijd
    weekday = now_et.weekday() < 5
    minutes = now_et.hour * 60 + now_et.minute
    signals: list[dict] = []

    # Crypto: 24/7
    try:
        signals += scan_crypto()
    except Exception as e:
        log(f"Crypto scan mislukt: {e}")

    # Goud: elk uur (alleen gesloten candles tellen)
    try:
        signals += scan_gold(now_et)
    except Exception as e:
        log(f"Goud scan mislukt: {e}")

    # Stocks daily + universum: één keer per dag na de slotbel (of als er nog geen universum is)
    today = now_et.date().isoformat()
    after_close = weekday and minutes >= 16 * 60 + 15
    universe = state.get("universe") or {}
    needs_universe = not universe or "vol" not in next(iter(universe.values()))
    if (after_close and state.get("stocks_daily_done") != today) or needs_universe:
        try:
            signals += scan_stocks_daily(state, now_et)
            if after_close:
                state["stocks_daily_done"] = today
        except Exception as e:
            log(f"Stocks daily scan mislukt: {e}")

    # Stocks 4h: tijdens beurstijden (candles sluiten om 13:30 en 16:00)
    if weekday and 13 * 60 + 25 <= minutes <= 17 * 60 + 30 and state.get("universe"):
        try:
            signals += scan_stocks_intraday(state, now_et)
        except Exception as e:
            log(f"Stocks intraday scan mislukt: {e}")

    # Dubbele meldingen voorkomen
    new = []
    for s in signals:
        key = f"{s['name']}|{s['tf']}"
        stamp = pd.Timestamp(s["time"]).isoformat()
        if state["alerts"].get(key, "") < stamp:
            state["alerts"][key] = stamp
            new.append(s)

    new.sort(key=lambda s: s.get("vol", 0.0), reverse=True)  # hoogste volume eerst
    messages = [format_signal(s) for s in new]
    log(f"{len(signals)} signalen, {len(new)} nieuw")
    for m in messages:
        log(m.replace("\n", " | "))

    if dry_run:
        return 0
    if messages:
        send_telegram(messages)

    # Oude meldingen (> 60 dagen) opruimen
    cutoff = (now_utc - timedelta(days=60)).isoformat()
    state["alerts"] = {k: v for k, v in state["alerts"].items() if v >= cutoff[:10]}
    STATE_FILE.write_text(json.dumps(state, indent=0, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
