"""HH + HL trend scanner voor US stocks, crypto (Binance) en goud.

Draait elk half uur (GitHub Actions) en stuurt een Telegram-bericht zodra een
ticker een nieuwe uptrend maakt: Higher High + Higher Low.
Timeframes stel je in met de GitHub-variabele TIMEFRAMES (zie README).
Meldingen staan gesorteerd op dagvolume in dollar (hoogste eerst).

Gebruik:
    python scanner/scan.py                 # normale run
    python scanner/scan.py --dry-run       # alleen printen, niets versturen/opslaan
    python scanner/scan.py --test-telegram # stuur een testbericht
"""

from __future__ import annotations

import io
import json
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

# Timeframes + aantal candles links en rechts om een top/bodem te bevestigen.
# In te stellen via de omgevingsvariabele TIMEFRAMES, bv. "4h:2,1d:2".
SUPPORTED_TF = ("1h", "2h", "4h", "1d", "1w")
DEFAULT_TIMEFRAMES = "4h:2,1d:2"
TV_INTERVAL = {"1h": "60", "2h": "120", "4h": "240", "1d": "D", "1w": "W"}


def parse_timeframes(text: str) -> dict[str, int]:
    out = {}
    for part in (text or "").replace(" ", "").lower().split(","):
        if not part:
            continue
        tf, _, n = part.partition(":")
        tf = {"d": "1d", "w": "1w", "1day": "1d", "1week": "1w"}.get(tf, tf)
        if tf not in SUPPORTED_TF:
            print(f"Onbekende timeframe '{tf}' genegeerd (kies uit {', '.join(SUPPORTED_TF)})")
            continue
        out[tf] = max(1, min(10, int(n))) if n.isdigit() else 2
    return out or parse_timeframes(DEFAULT_TIMEFRAMES)


PIVOT_LEN = parse_timeframes(os.environ.get("TIMEFRAMES", ""))
INTRADAY_HOURS = {tf: int(tf[:-1]) for tf in PIVOT_LEN if tf.endswith("h")}

# Filter voor US stocks.
MIN_PRICE = 5.0
MIN_AVG_VOLUME = 500_000  # gemiddeld aantal aandelen per dag over 20 dagen


def parse_amount(text: str, default: float) -> float:
    """'10m' -> 10_000_000, '500k' -> 500_000, '1.5b' -> 1_500_000_000."""
    t = (text or "").strip().lower().replace("$", "").replace(",", "").replace(" ", "")
    if not t:
        return default
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get(t[-1], 1)
    try:
        return float(t[:-1] if mult != 1 else t) * mult
    except ValueError:
        print(f"Ongeldige MIN_VOLUME '{text}', standaard gebruikt")
        return default


# Minimum dagvolume in dollar (mediaan over 20 dagen) voor elke melding.
# In te stellen via de omgevingsvariabele MIN_VOLUME, bv. "10m".
MIN_DOLLAR_VOLUME = parse_amount(os.environ.get("MIN_VOLUME", ""), 10e6)

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
    """Zoekt een nieuwe Higher High die op een Higher Low volgt (L1 -> H1 -> L2 -> H2).

    Een top (pivot high) is bevestigd als er `length` lagere candles links en
    rechts van liggen. Bij elke nieuwe bevestigde top H2 kijken we:
      - H2 > H1 (de vorige top)                       -> Higher High
      - L2 > L1, met L2 = laagste low tussen H1 en H2  -> Higher Low
                 en  L1 = laagste low tussen H0 en H1
    Elke nieuwe HH die hieraan voldoet is een signaal (dus ook verderop in de trend).
    De melding komt op de candle waarop H2 bevestigd wordt.
    """
    n = len(df)
    if n < 4 * length + 2:
        return None
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    tops: list[int] = []  # candle-indexen van bevestigde toppen
    signal = None

    for j in range(2 * length, n):  # j = candle waarop de top bevestigd wordt
        i = j - length
        if not (h[i] > h[i - length:i].max() and h[i] >= h[i + 1:j + 1].max()):
            continue
        tops.append(i)
        if len(tops) < 3:
            continue
        h0, h1, h2 = tops[-3:]
        l1 = l[h0 + 1:h1].min()
        l2 = l[h1 + 1:h2].min()
        if h[h2] > h[h1] and l2 > l1:
            signal = {"bar": j, "hh": h[h2], "prev_h": h[h1], "hl": l2, "prev_l": l1}

    if signal and signal["bar"] >= n - RECENT_BARS:
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
                [[float(x) for x in k[1:6]] + [float(k[7])] for k in rows],
                columns=["open", "high", "low", "close", "volume", "quote_volume"],
                index=pd.to_datetime([k[0] for k in rows], unit="ms", utc=True),
            )
            return df
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    return None


def scan_crypto() -> list[dict]:
    symbols = binance_symbols()
    now_ms = int(time.time() * 1000)

    # Mediaan van het dagvolume (USDT) over 20 gesloten dagen, zoals bij stocks:
    # een paar pump-dagen tillen een munt niet boven het minimum.
    with ThreadPoolExecutor(max_workers=8) as pool:
        daily = dict(zip(symbols, pool.map(lambda s: binance_klines(s, "1d", now_ms), symbols)))
    volumes = {s: float(df["quote_volume"].tail(20).median())
               for s, df in daily.items() if df is not None and len(df)}
    active = [s for s in symbols if volumes.get(s, 0.0) >= MIN_DOLLAR_VOLUME]
    log(f"Crypto: {len(symbols)} USDT-paren, {len(active)} met dagvolume "
        f"> {fmt_vol(MIN_DOLLAR_VOLUME)} (20 dagen)")
    jobs = [(s, tf) for s in active for tf in PIVOT_LEN]

    def work(job):
        sym, tf = job
        df = daily[sym] if tf == "1d" else binance_klines(sym, tf, now_ms)
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


def stock_hours(df1h: pd.DataFrame, hours: int, now: datetime) -> pd.DataFrame:
    """Nh-candles zoals TradingView: vanaf 9:30, laatste candle stopt om 16:00 (New York).
    Bv. 4h = 9:30-13:30 en 13:30-16:00."""
    if df1h.empty or hours == 1:
        return df1h
    day = df1h.index.normalize()
    slot = ((df1h.index.hour * 60 + df1h.index.minute) - 570) // (hours * 60)
    starts = pd.Series(day + pd.Timedelta(minutes=570)
                       + pd.to_timedelta(slot * hours, unit="h"), index=df1h.index)
    df = aggregate(df1h, starts)
    ends = pd.Series([min(s + pd.Timedelta(hours=hours), s.normalize() + pd.Timedelta(hours=16))
                      for s in df.index], index=df.index)
    return closed_only(df, ends, now)


def stock_1d(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    ends = pd.Series(df.index.normalize() + pd.Timedelta(hours=16), index=df.index)
    return closed_only(df, ends, now)


def gold_1h(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    df = df[df.index.minute == 0]
    return closed_only(df, pd.Series(df.index + pd.Timedelta(hours=1), index=df.index), now)


def gold_hours(df1h: pd.DataFrame, hours: int, now: datetime) -> pd.DataFrame:
    """Nh-candles zoals TradingView COMEX: sessie start 18:00 ET (4h -> 18, 22, 2, 6, 10, 14),
    pauze 17:00-18:00."""
    if df1h.empty or hours == 1:
        return df1h
    offset = pd.Timedelta(hours=18 % hours)
    starts = pd.Series((df1h.index - offset).floor(f"{hours}h") + offset, index=df1h.index)
    df = aggregate(df1h, starts)
    ends = []
    for s in df.index:
        end = s + pd.Timedelta(hours=hours)
        pause = s.normalize() + pd.Timedelta(hours=17)
        ends.append(pause if s < pause < end else end)
    return closed_only(df, pd.Series(ends, index=df.index), now)


def gold_1d(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    ends = pd.Series(df.index.normalize() + pd.Timedelta(hours=17), index=df.index)
    return closed_only(df, ends, now)


def weekly(df1d: pd.DataFrame, close_hour: int, now: datetime) -> pd.DataFrame:
    """Weekcandles (maandag t/m vrijdag) uit gesloten daily candles."""
    if df1d.empty:
        return df1d
    monday = df1d.index.normalize() - pd.to_timedelta(df1d.index.weekday, unit="D")
    df = aggregate(df1d, pd.Series(monday, index=df1d.index))
    ends = pd.Series(df.index + pd.Timedelta(days=4, hours=close_hour), index=df.index)
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
    frames = yahoo_download(list(candidates), "2y" if "1w" in PIVOT_LEN else "1y", "1d")
    universe, signals = {}, []
    for t, df in frames.items():
        df = stock_1d(df, now)
        if len(df) < 20:
            continue
        dollar_vol = float((df["close"] * df["volume"]).tail(20).median())
        if (df["close"].iloc[-1] < MIN_PRICE or df["volume"].tail(20).mean() < MIN_AVG_VOLUME
                or dollar_vol < MIN_DOLLAR_VOLUME):
            continue
        universe[t] = {**candidates[t], "vol": dollar_vol}
        sets = []
        if "1d" in PIVOT_LEN:
            sets.append(("1d", df))
        if "1w" in PIVOT_LEN:
            sets.append(("1w", weekly(df, 16, now)))
        for tf, data in sets:
            sig = find_signal(data, PIVOT_LEN[tf])
            if sig:
                sig.update(tf=tf, **universe[t])
                signals.append(sig)
    log(f"Stocks: {len(universe)} na filter (prijs > ${MIN_PRICE:g}, volume > {MIN_AVG_VOLUME:,}, "
        f"dagvolume > {fmt_vol(MIN_DOLLAR_VOLUME)})")
    if universe:  # bij een mislukte download het oude universum houden
        state["universe"] = universe
        state["universe_min_vol"] = MIN_DOLLAR_VOLUME
    return signals


def scan_stocks_intraday(state: dict, now: datetime) -> list[dict]:
    universe = state.get("universe", {})
    log(f"Stocks intraday: {len(universe)} tickers")
    frames = yahoo_download(list(universe), "60d", "1h")
    signals = []
    for t, df in frames.items():
        h1 = stock_1h(df, now)
        for tf, hours in INTRADAY_HOURS.items():
            sig = find_signal(stock_hours(h1, hours, now), PIVOT_LEN[tf])
            if sig:
                sig.update(tf=tf, **universe[t])
                signals.append(sig)
    return signals


def scan_gold(now: datetime) -> list[dict]:
    signals = []
    h = yahoo_download([GOLD["yahoo"]], "60d", "1h").get(GOLD["yahoo"]) if INTRADAY_HOURS else None
    d = yahoo_download([GOLD["yahoo"]], "2y", "1d").get(GOLD["yahoo"])
    sets, vol = [], 0.0
    if h is not None:
        h1 = gold_1h(h, now)
        sets += [(tf, gold_hours(h1, hours, now)) for tf, hours in INTRADAY_HOURS.items()]
    if d is not None:
        d = gold_1d(d, now)
        if "1d" in PIVOT_LEN:
            sets.append(("1d", d))
        if "1w" in PIVOT_LEN:
            sets.append(("1w", weekly(d, 17, now)))
        vol = float((d["close"] * d["volume"] * 100).tail(20).median())  # 100 oz per contract
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
    chart = f"https://www.tradingview.com/chart/?symbol={s['tv']}&interval={TV_INTERVAL[s['tf']]}"
    ticker = s["tv"].split(":")[-1]  # zoals je hem in TradingView intypt
    return (f"🔔 <code>{ticker}</code> HH + HL op <b>{s['tf']}</b> · Vol {fmt_vol(s.get('vol', 0))}/dag\n"
            f"HH {fmt(s['hh'])} (vorige {fmt(s['prev_h'])}) · "
            f"HL {fmt(s['hl'])} (vorige {fmt(s['prev_l'])})\n"
            f"Koers {fmt(s['close'])} · <a href=\"{chart}\">Chart</a>")


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
    log("Timeframes: " + ", ".join(f"{tf} ({n} candles)" for tf, n in PIVOT_LEN.items()))
    log(f"Minimum dagvolume: {fmt_vol(MIN_DOLLAR_VOLUME)}")

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
    needs_universe = (not universe or "vol" not in next(iter(universe.values()))
                      or state.get("universe_min_vol") != MIN_DOLLAR_VOLUME)
    if (after_close and state.get("stocks_daily_done") != today) or needs_universe:
        try:
            signals += scan_stocks_daily(state, now_et)
            if after_close:
                state["stocks_daily_done"] = today
        except Exception as e:
            log(f"Stocks daily scan mislukt: {e}")

    # Stocks intraday: tijdens beurstijden, vanaf de eerste candle die sluit (bv. 4h: 13:30)
    first_close = 9 * 60 + 30 + 60 * min(INTRADAY_HOURS.values(), default=0)
    if (INTRADAY_HOURS and weekday and first_close - 5 <= minutes <= 17 * 60 + 30
            and state.get("universe")):
        try:
            signals += scan_stocks_intraday(state, now_et)
        except Exception as e:
            log(f"Stocks intraday scan mislukt: {e}")

    # Te weinig volume: geen melding
    signals = [s for s in signals if s.get("vol", 0.0) >= MIN_DOLLAR_VOLUME]

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
