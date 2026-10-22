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
import math
import os
import re
import sys
import time
import warnings
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
DEFAULT_TIMEFRAMES = "4h:2"
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


# Een nieuwe top telt pas als HH als hij minstens zoveel % boven de vorige top ligt
# (anders is het eerder een dubbele top). In te stellen via MIN_HH_PCT, bv. "1.5".
try:
    MIN_HH_PCT = float(os.environ.get("MIN_HH_PCT", "").replace(",", ".") or 1.0)
except ValueError:
    MIN_HH_PCT = 1.0

# Chart-kwaliteit (alleen voor het rapport, geen filter):
#  - body: mediaan van |close-open| / (high-low). Hoog = volle candles, weinig lange wicks.
#  - gaps: gemiddelde |open - vorige close| / gemiddelde candle-range. Laag = weinig gaps.
QUALITY_BARS = 50

# Minimum dagvolume in dollar (mediaan over 20 dagen) voor elke melding.
# In te stellen via de omgevingsvariabele MIN_VOLUME, bv. "10m".
MIN_DOLLAR_VOLUME = parse_amount(os.environ.get("MIN_VOLUME", ""), 10e6)
# Aparte (hogere) grens voor US stocks: minder wicks en gaps. Variabele MIN_VOLUME_STOCKS.
MIN_STOCK_VOLUME = parse_amount(os.environ.get("MIN_VOLUME_STOCKS", ""), 10e6)

# Halal-filter voor stocks volgens AAOIFI en MSCI Islamic (gegevens: SEC EDGAR).
# Uitzetten: variabele HALAL=0.
HALAL_ONLY = os.environ.get("HALAL", "1").strip().lower() not in ("0", "false", "no", "nee", "off")
# Welke stocks doorlaten (variabele HALAL_METHOD): "any" = minstens één methode (standaard),
# "both" = beide, "aaoifi" of "msci" = alleen die methode.
HALAL_METHOD = (os.environ.get("HALAL_METHOD") or "any").strip().lower()
if HALAL_METHOD not in ("any", "both", "aaoifi", "msci"):
    HALAL_METHOD = "any"
MSCI_MAX_RATIO = 1 / 3           # schulden, cash+beleggingen / totale activa
AAOIFI_MAX_RATIO = 0.30          # schulden, cash+beleggingen / beurswaarde
HALAL_REFRESH_DAYS = 7           # balansgegevens opnieuw ophalen
SIC_REFRESH_DAYS = 30            # sectorcode per bedrijf opnieuw ophalen
# De SEC vraagt "naam e-mailadres" in de User-Agent (anders 403). Eigen waarde kan via
# de GitHub-secret SEC_USER_AGENT; anders worden deze in volgorde geprobeerd.
SEC_AGENTS = [a for a in (os.environ.get("SEC_USER_AGENT"),
                          "HH-HL-Scanner Ahmed-132@users.noreply.github.com",
                          "HH-HL-Scanner admin@hhhlscanner.com") if a]
# Niet-toegelaten activiteiten via SIC-codes (SEC-sectorcodes).
HALAL_EXCLUDED_SIC = [
    (2011, 2013, "vlees/varkensvlees"), (2082, 2085, "alcohol"), (2100, 2199, "tabak"),
    (3480, 3489, "wapens"), (3760, 3769, "wapens/raketten"), (3795, 3795, "wapens"),
    (6000, 6411, "bank/verzekering/financiële diensten"), (7011, 7011, "hotels/casino's"),
    (7800, 7849, "film/entertainment"), (7990, 7999, "amusement/entertainment/gokken"),
]
# Bedrijven die de SIC-code niet vangt (bv. alcohol onder 'beverages', defensie).
HALAL_EXCLUDED_TICKERS = {
    "STZ": "alcohol", "BF-A": "alcohol", "BF-B": "alcohol", "TAP": "alcohol", "DEO": "alcohol",
    "BUD": "alcohol", "SAM": "alcohol", "MGPI": "alcohol", "ABEV": "alcohol",
    "LMT": "wapens", "NOC": "wapens", "GD": "wapens", "RTX": "wapens", "LHX": "wapens",
    "HII": "wapens", "TSN": "varkensvlees", "HRL": "varkensvlees",
}

# Een signaal telt als het op een van de laatste N gesloten candles ontstond
# (vangt een gemiste of vertraagde run op; dubbele meldingen worden voorkomen).
RECENT_BARS = 3
SIGNAL_MAX_AGE = RECENT_BARS  # momentopname-modus zet dit hoger

GOLD = {"yahoo": "GC=F", "name": "GOLD", "tv": "COMEX:GC1!"}

STATE_FILE = Path(__file__).with_name("state.json")
ET = ZoneInfo("America/New_York")
BINANCE = "https://data-api.binance.vision/api/v3"  # publieke data, geen geo-block
NASDAQ_LIST = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

STABLECOINS = {
    "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "PAX", "EUR", "EURI", "AEUR",
    "GBP", "USDE", "XUSD", "BFUSD", "USD1", "RLUSD", "PYUSD", "UST", "USDS", "U",
}
# Crypto die niet toegelaten is: munten van platformen die uitlenen tegen rente of
# rentedragende producten maken, en munten van gokplatformen. Uitzetten: CRYPTO_HALAL=0.
CRYPTO_HALAL = os.environ.get("CRYPTO_HALAL", "1").strip().lower() not in ("0", "false", "no", "nee", "off")
CRYPTO_EXCLUDED = {
    **dict.fromkeys(["AAVE", "COMP", "MKR", "SKY", "XVS", "JST", "QI", "RDNT", "MORPHO", "KMNO",
                     "EUL", "SPELL", "PENDLE", "LISTA", "ALPACA", "CREAM", "TRU", "SYRUP",
                     "ONDO"], "rente/uitlenen"),
    **dict.fromkeys(["WIN", "FUN", "DICE", "RLB", "SHFL"], "gokken"),
    **dict.fromkeys(["WLFI"], "rente/uitlenen"),
    # Meme-coins: geen nut of project, prijs alleen door hype (gokken/gharar).
    **dict.fromkeys(["DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "MEME", "BOME", "1000SATS",
                     "TURBO", "NEIRO", "PEOPLE", "1MBABYDOGE", "BABYDOGE", "TRUMP", "PNUT", "ACT",
                     "GOAT", "MOODENG", "BRETT", "POPCAT", "DOGS", "PENGU", "MUBARAK",
                     "BROCCOLI714", "TST", "1000CHEEMS", "1000CAT", "TUT", "BANANAS31", "PUMP",
                     "CHILLGUY", "SPX", "FARTCOIN", "MELANIA", "HIPPO", "LUNC", "USTC", "BANANA",
                     "MEW", "SLERF", "MYRO", "BOOK", "WOJAK", "LADYS", "AIDOGE", "ELON", "CAT",
                     "SUNDOG", "PONKE", "GIGA", "MOG", "BABY", "DEGEN"], "meme-coin"),
}
TV_EXCHANGE = {"Q": "NASDAQ", "N": "NYSE", "A": "AMEX", "P": "AMEX", "Z": "CBOE"}
JUNK_NAME = re.compile(
    r"warrant|\bunits?\b|\brights?\b|preferred|\bnotes?\b|debenture|%", re.I
)

# yfinance geeft veel pandas-waarschuwingen (onschuldig); die maken het log onleesbaar.
warnings.filterwarnings("ignore")

http = requests.Session()
http.headers["User-Agent"] = "Mozilla/5.0 (hh-hl-scanner)"


def log(*args):
    print(*args, flush=True)


# ------------------------------------------------------------------ trendlogica


def chart_quality(df: pd.DataFrame, bars: int = QUALITY_BARS) -> tuple[float, float]:
    """(body, gaps) over de laatste candles."""
    d = df.tail(bars + 1)
    rng = d["high"] - d["low"]
    body = float(((d["close"] - d["open"]).abs() / rng.where(rng > 0)).iloc[1:].median())
    gaps = float((d["open"] - d["close"].shift()).abs().iloc[1:].mean() / rng.iloc[1:].mean())
    return body, gaps


def find_signal(df: pd.DataFrame, length: int) -> dict | None:
    r"""Trendomkeer: de prijs breekt boven de laatste Lower High en maakt daarna een Higher Low.

        H0                                  
          \    LH                 H  (nieuwe top na de break)
           \  /  \    break -->  /\
            \/    \  ---------- /  \
            L0     \          /    HL   <- melding als deze bodem bevestigd is
                    \        /
                     LL ----

    - LH  = laatste top vóór de break, lager dan de top ervoor (dus een dalende trend).
    - Break = eerste candle die boven de LH SLUIT.
    - LL  = laagste punt tussen LH en de break.
    - HL  = eerste bevestigde bodem na de break, hoger dan LL.
    - De nieuwe top na de break moet minstens MIN_HH_PCT % boven de LH liggen.
    Melding op de candle waarop de HL bevestigd wordt (`length` candles na de bodem),
    zolang de prijs nog niet onder de HL is gezakt.
    """
    n = len(df)
    if n < 6 * length + 4:
        return None
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)

    def is_top(i: int) -> bool:
        return h[i] > h[i - length:i].max() and h[i] >= h[i + 1:i + length + 1].max()

    def is_bottom(i: int) -> bool:
        return l[i] < l[i - length:i].min() and l[i] <= l[i + 1:i + length + 1].min()

    tops = [i for i in range(length, n - length) if is_top(i)]
    bottoms = [i for i in range(length, n - length) if is_bottom(i)]
    signal = None
    for k in bottoms:  # kandidaat-HL
        # Laatste top vóór k die vóór k gebroken werd (slot erboven) = de LH.
        prior = [a for a in tops if a < k]
        lh = next((a for a in reversed(prior) if c[a + 1:k].max(initial=-math.inf) > h[a]), None)
        if lh is None:
            continue
        idx = prior.index(lh)
        if idx == 0 or h[lh] >= h[prior[idx - 1]]:
            continue  # geen Lower High: de trend was niet dalend
        b = lh + 1 + int((c[lh + 1:k] > h[lh]).argmax())  # eerste slot boven de LH
        if any(b < x < k for x in bottoms):
            continue  # k is niet de eerste bodem na de break
        ll_i = lh + 1 + int(l[lh + 1:b + 1].argmin())
        top_i = b + int(h[b:k].argmax())
        if l[k] <= l[ll_i] or h[top_i] < h[lh] * (1 + MIN_HH_PCT / 100):
            continue
        signal = {"bar": k + length, "hl_i": k, "hh": h[top_i], "prev_h": h[lh], "hl": l[k],
                  "prev_l": l[ll_i],
                  "pts": {"LH": (lh, h[lh]), "LL": (ll_i, l[ll_i]), "H": (top_i, h[top_i]),
                          "HL": (k, l[k])},
                  "break_i": b}

    if not signal:
        return None
    # HL al gebroken of te oud? Geen melding.
    if l[signal["hl_i"] + 1:].min() < signal["hl"]:
        return None
    if signal["bar"] >= n - SIGNAL_MAX_AGE:
        signal["time"] = df.index[signal["bar"]]
        signal["close"] = float(c[-1])
        signal["chart"] = df  # voor de afbeelding
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
                and not (CRYPTO_HALAL and base in CRYPTO_EXCLUDED)
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


def universe_cfg() -> str:
    return f"{MIN_STOCK_VOLUME:g}|v2"


def sec_get(url: str):
    for attempt in range(3):
        r = requests.get(url, timeout=60, headers={
            "User-Agent": SEC_AGENTS[0], "Accept-Encoding": "gzip, deflate"})
        if r.status_code == 404:
            return None
        if r.ok:
            return r.json()
        if r.status_code == 403 and len(SEC_AGENTS) > 1:
            SEC_AGENTS.pop(0)  # User-Agent geweigerd: volgende proberen
            continue
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()


def recent_quarters(n: int = 3) -> list[str]:
    """Laatste n afgesloten kalenderkwartalen, nieuwste eerst, bv. ['CY2026Q3I', ...]."""
    d = datetime.now(timezone.utc)
    y, q = d.year, (d.month - 1) // 3  # q = vorig kwartaal (0 = Q4 vorig jaar)
    out = []
    for _ in range(n):
        if q == 0:
            y, q = y - 1, 4
        out.append(f"CY{y}Q{q}I")
        q -= 1
    return out


BALANCE_TAGS = ["Assets", "LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtCurrent",
                "LongTermDebtAndCapitalLeaseObligations", "ShortTermBorrowings",
                "CashAndCashEquivalentsAtCarryingValue", "ShortTermInvestments",
                "MarketableSecuritiesCurrent"]


def sec_balances() -> dict[int, dict[str, float]]:
    """Balansposten per CIK uit het meest recente kwartaal waarin 'Assets' bekend is."""
    by_q: dict[str, dict[str, dict[int, float]]] = {}
    for q in recent_quarters():
        by_q[q] = {}
        for tag in BALANCE_TAGS:
            data = sec_get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/{q}.json")
            by_q[q][tag] = {d["cik"]: float(d["val"]) for d in (data or {}).get("data", [])}
            time.sleep(0.15)
    out: dict[int, dict[str, float]] = {}
    for q in recent_quarters():  # nieuwste eerst
        for cik, assets in by_q[q]["Assets"].items():
            if cik not in out and assets > 0:
                out[cik] = {tag: vals[cik] for tag, vals in by_q[q].items() if cik in vals}
    return out


SHARE_TAGS = ["WeightedAverageNumberOfDilutedSharesOutstanding",
              "WeightedAverageNumberOfSharesOutstandingBasic"]


def sec_shares() -> dict[int, float]:
    """Aantal aandelen per CIK (alle aandelenklassen samen), meest recente kwartaal."""
    out: dict[int, float] = {}
    for q in recent_quarters():
        for tag in SHARE_TAGS:
            data = sec_get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/shares/{q[:-1]}.json")
            for d in (data or {}).get("data", []):
                if d["cik"] not in out and d["val"] > 0:
                    out[d["cik"]] = float(d["val"])
            time.sleep(0.15)
    return out


def halal_reasons(ticker: str, sic: int | None, bal: dict[str, float] | None,
                  shares: float | None, price: float | None) -> dict[str, str]:
    """Per methode een lege string (= halal) of de reden van uitsluiting."""
    def both(why: str) -> dict[str, str]:
        return {"aaoifi": why, "msci": why}

    if ticker in HALAL_EXCLUDED_TICKERS:
        return both(HALAL_EXCLUDED_TICKERS[ticker])
    if sic is None:
        return both("geen SEC-gegevens")
    for lo, hi, why in HALAL_EXCLUDED_SIC:
        if lo <= sic <= hi:
            return both(why)
    if not bal:
        return both("geen balans (US-GAAP)")
    g = lambda k: bal.get(k, 0.0)
    debt = (g("LongTermDebt") or g("LongTermDebtAndCapitalLeaseObligations")
            or g("LongTermDebtNoncurrent") + g("LongTermDebtCurrent")) + g("ShortTermBorrowings")
    cash = g("CashAndCashEquivalentsAtCarryingValue") + (g("ShortTermInvestments")
                                                         or g("MarketableSecuritiesCurrent"))

    def ratios(base: float, limit: float, label: str) -> str:
        if debt / base >= limit:
            return f"schulden {debt / base:.0%} van {label}"
        if cash / base >= limit:
            return f"cash {cash / base:.0%} van {label}"
        return ""

    mcap = (shares or 0) * (price or 0)
    return {"msci": ratios(bal["Assets"], MSCI_MAX_RATIO, "activa"),
            "aaoifi": ratios(mcap, AAOIFI_MAX_RATIO, "beurswaarde") if mcap > 0
            else "geen beurswaarde"}


def halal_check_report(tickers: list[str]) -> list[str]:
    """Controleert losse tickers en geeft per ticker een leesbaar resultaat (voor Telegram)."""
    tickers = [t.strip().upper().replace(".", "-") for t in tickers if t.strip()]
    closes = {t: float(df["close"].iloc[-1]) for t, df in yahoo_download(tickers, "1mo", "1d").items()}
    rows = sec_get("https://www.sec.gov/files/company_tickers.json") or {}
    cik_of = {r["ticker"].upper().replace(".", "-"): int(r["cik_str"]) for r in rows.values()}
    balances, shares = sec_balances(), sec_shares()
    out = []
    for t in tickers:
        cik = cik_of.get(t)
        sub = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json") if cik else {}
        sic = int(sub["sic"]) if str((sub or {}).get("sic", "")).isdigit() else None
        bal = balances.get(cik) if cik else None
        sh, price = (shares.get(cik) if cik else None), closes.get(t)
        res = halal_reasons(t, sic, bal, sh, price)
        lines = [f"<b>{t}</b> — {(sub or {}).get('sicDescription', '?')} (SIC {sic})"]
        if bal:
            g = lambda k: bal.get(k, 0.0)
            debt = (g("LongTermDebt") or g("LongTermDebtAndCapitalLeaseObligations")
                    or g("LongTermDebtNoncurrent") + g("LongTermDebtCurrent")) + g("ShortTermBorrowings")
            cash = g("CashAndCashEquivalentsAtCarryingValue") + (g("ShortTermInvestments")
                                                                 or g("MarketableSecuritiesCurrent"))
            mcap = (sh or 0) * (price or 0)
            lines.append(f"Schulden {fmt_vol(debt)} · cash+beleggingen {fmt_vol(cash)} · "
                         f"activa {fmt_vol(bal['Assets'])} · beurswaarde {fmt_vol(mcap)}")
        for key, name in (("aaoifi", "AAOIFI"), ("msci", "MSCI")):
            why = res[key]
            lines.append(f"{name}: {'✅ halal' if not why else '❌ ' + why}")
        out.append("\n".join(lines))
        time.sleep(0.12)
    return out


def quality_report(state: dict) -> list[str]:
    """Verdeling van body/gaps op 4h voor crypto en stocks, om de grenzen te kiezen."""
    now_ms = int(time.time() * 1000)
    now_et = datetime.now(timezone.utc).astimezone(ET).replace(tzinfo=None)
    groups: dict[str, dict[str, tuple[float, float]]] = {"crypto": {}, "stocks": {}}
    syms = binance_symbols()
    with ThreadPoolExecutor(max_workers=8) as pool:
        daily = dict(zip(syms, pool.map(lambda s: binance_klines(s, "1d", now_ms), syms)))
        active = [s for s, df in daily.items() if df is not None and len(df)
                  and df["quote_volume"].tail(20).median() >= MIN_DOLLAR_VOLUME]
        for s, df in zip(active, pool.map(lambda s: binance_klines(s, "4h", now_ms), active)):
            if df is not None and len(df) > QUALITY_BARS:
                groups["crypto"][s] = chart_quality(df)
    universe = state.get("universe") or state.get("liquid") or {}  # na halal-filter
    for t, df in yahoo_download(list(universe), "60d", "1h").items():
        d4 = stock_hours(stock_1h(df, now_et), 4, now_et)
        if len(d4) > QUALITY_BARS:
            groups["stocks"][t] = chart_quality(d4)
    out = []
    for name, vals in groups.items():
        if not vals:
            continue
        body = pd.Series({k: v[0] for k, v in vals.items()})
        gaps = pd.Series({k: v[1] for k, v in vals.items()})
        q = lambda x: " / ".join(f"{x.quantile(p):.0%}" for p in (0.1, 0.25, 0.5, 0.75, 0.9))
        score = body - gaps
        out.append(
            f"<b>{name}</b> ({len(vals)} tickers, 4h, laatste {QUALITY_BARS} candles)\n"
            f"body p10/p25/p50/p75/p90: {q(body)}\n"
            f"gaps p10/p25/p50/p75/p90: {q(gaps)}\n"
            f"mooiste: {', '.join(score.nlargest(8).index)}\n"
            f"lelijkste: {', '.join(score.nsmallest(8).index)}")
    return out


def halal_pass(res: dict[str, str] | None) -> bool:
    if not isinstance(res, dict):
        return False
    a, m = res.get("aaoifi") == "", res.get("msci") == ""
    return {"any": a or m, "both": a and m, "aaoifi": a, "msci": m}[HALAL_METHOD]


def halal_label(res: dict[str, str]) -> str:
    return " · ".join(f"{name} {'✅' if res.get(key) == '' else '❌'}"
                      for key, name in (("aaoifi", "AAOIFI"), ("msci", "MSCI")))


def halal_universe(liquid: dict, halal: dict) -> dict:
    return {t: {**v, "halal": halal_label(halal[t])} for t, v in liquid.items()
            if halal_pass(halal.get(t))}


def apply_halal(state: dict) -> None:
    """Zet state['universe'] = liquide stocks die halal zijn (of alle liquide als het filter uit staat)."""
    liquid = state.get("liquid", {})
    if not HALAL_ONLY:
        state["universe"] = liquid
        return
    today = datetime.now(timezone.utc).date()
    fresh = state.get("halal_date", "") >= (today - timedelta(days=HALAL_REFRESH_DAYS)).isoformat()
    halal = state.get("halal", {})
    if state.get("halal_ver") != 2:
        fresh, halal = False, {}
    if fresh and all(t in halal for t in liquid):
        state["universe"] = halal_universe(liquid, halal)
        return
    try:
        log(f"Halal: {len(liquid)} stocks controleren via SEC...")
        tickers = sec_get("https://www.sec.gov/files/company_tickers.json") or {}
        cik_of = {row["ticker"].upper().replace(".", "-"): int(row["cik_str"])
                  for row in tickers.values()}
        sic_cache = state.setdefault("sic", {})  # cik -> [sic, datum]
        sic_cutoff = (today - timedelta(days=SIC_REFRESH_DAYS)).isoformat()
        for t in liquid:
            cik = cik_of.get(t)
            if cik and sic_cache.get(str(cik), [None, ""])[1] < sic_cutoff:
                sub = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json") or {}
                sic = int(sub["sic"]) if str(sub.get("sic", "")).isdigit() else None
                sic_cache[str(cik)] = [sic, today.isoformat()]
                time.sleep(0.12)
        balances = sec_balances()
        shares = sec_shares()
    except Exception as e:
        log(f"Halal: SEC-gegevens ophalen mislukt ({e}), vorige resultaten gebruikt")
        state["universe"] = halal_universe(liquid, {t: halal.get(t) for t in liquid if t in halal})
        return
    halal = {}
    for t, v in liquid.items():
        cik = cik_of.get(t)
        sic = sic_cache.get(str(cik), [None])[0] if cik else None
        halal[t] = halal_reasons(t, sic, balances.get(cik) if cik else None,
                                 shares.get(cik) if cik else None, v.get("price"))
    state["halal"], state["halal_date"], state["halal_ver"] = halal, today.isoformat(), 2
    state["universe"] = halal_universe(liquid, halal)
    for key, name in (("aaoifi", "AAOIFI"), ("msci", "MSCI")):
        reasons: dict[str, int] = {}
        for res in halal.values():
            why = res[key]
            if why:
                k = " ".join(why.split(" ")[:1] + why.split(" ")[2:]) if "%" in why else why
                reasons[k] = reasons.get(k, 0) + 1
        ok = sum(1 for res in halal.values() if res[key] == "")
        log(f"Halal {name}: {ok} van {len(liquid)} ok · uitgesloten: "
            + ", ".join(f"{k} {n}" for k, n in sorted(reasons.items(), key=lambda x: -x[1])))
    log(f"Halal: {len(state['universe'])} stocks doorgelaten (methode: {HALAL_METHOD})")


def scan_stocks_daily(state: dict, now: datetime) -> list[dict]:
    """Bouwt het universum (prijs/volume-filter) en zoekt daily-signalen."""
    candidates = stock_candidates()
    log(f"Stocks: {len(candidates)} kandidaten, daily data ophalen...")
    frames = yahoo_download(list(candidates), "2y" if "1w" in PIVOT_LEN else "1y", "1d")
    universe, signals, daily = {}, [], {}
    for t, df in frames.items():
        df = stock_1d(df, now)
        if len(df) < 20:
            continue
        dollar_vol = float((df["close"] * df["volume"]).tail(20).median())
        if (df["close"].iloc[-1] < MIN_PRICE or df["volume"].tail(20).mean() < MIN_AVG_VOLUME
                or dollar_vol < MIN_STOCK_VOLUME):
            continue
        universe[t] = {**candidates[t], "vol": dollar_vol, "price": float(df["close"].iloc[-1])}
        daily[t] = df
    log(f"Stocks: {len(universe)} na filter (prijs > ${MIN_PRICE:g}, volume > {MIN_AVG_VOLUME:,}, "
        f"dagvolume > {fmt_vol(MIN_STOCK_VOLUME)})")
    if universe:  # bij een mislukte download het oude universum houden
        state["liquid"] = universe
        state["universe_cfg"] = universe_cfg()
    apply_halal(state)
    universe = state.get("universe", {})
    for t, info in universe.items():
        if t not in daily:
            continue
        df = daily[t]
        sets = []
        if "1d" in PIVOT_LEN:
            sets.append(("1d", df))
        if "1w" in PIVOT_LEN:
            sets.append(("1w", weekly(df, 16, now)))
        for tf, data in sets:
            sig = find_signal(data, PIVOT_LEN[tf])
            if sig:
                sig.update(tf=tf, **info)
                signals.append(sig)
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
    above = (s["close"] / s["hl"] - 1) * 100
    return (f"🔔 <code>{ticker}</code> trendomkeer op <b>{s['tf']}</b> · Vol {fmt_vol(s.get('vol', 0))}/dag\n"
            f"LH {fmt(s['prev_h'])} gebroken · nieuwe top {fmt(s['hh'])}\n"
            f"HL {fmt(s['hl'])} (LL was {fmt(s['prev_l'])})\n"
            f"Koers {fmt(s['close'])} ({above:+.1f}% t.o.v. HL) · <a href=\"{chart}\">Chart</a>")


def render_chart(s: dict) -> bytes:
    """PNG van de chart met LH, LL, break, H en HL gemarkeerd (TradingView-achtige donkere stijl)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df, pts = s["chart"], s["pts"]
    start = max(0, min(pts["LH"][0] - 15, len(df) - 70))
    d = df.iloc[start:]
    x = range(len(d))
    bg, up, down, grid, text = "#131722", "#26a69a", "#ef5350", "#2a2e39", "#d1d4dc"
    fig, ax = plt.subplots(figsize=(9, 5), dpi=110)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    width = 0.6
    for xi, (o, hi, lo, c) in zip(x, d[["open", "high", "low", "close"]].to_numpy(float)):
        col = up if c >= o else down
        ax.vlines(xi, lo, hi, color=col, linewidth=1)
        ax.add_patch(plt.Rectangle((xi - width / 2, min(o, c)), width, max(abs(c - o), 1e-12),
                                   color=col))
    px = {k: v[0] - start for k, v in pts.items()}
    py = {k: v[1] for k, v in pts.items()}
    order = ["LH", "LL", "H", "HL"]
    # Horizontale lijn op de LH tot aan de break
    ax.hlines(py["LH"], px["LH"], s["break_i"] - start, color="#2962ff", linewidth=1.2)
    ax.annotate("break", (s["break_i"] - start, py["LH"]), xytext=(4, -12),
                textcoords="offset points", color="#2962ff", fontsize=8)
    ax.plot([px[k] for k in order], [py[k] for k in order], color="#f0b90b", linewidth=1.6, zorder=3)
    for k in order:
        is_top = k in ("LH", "H")
        ax.annotate(k, (px[k], py[k]),
                    xytext=(0, 10 if is_top else -16), textcoords="offset points", ha="center",
                    color="#f0b90b", fontsize=9, fontweight="bold", zorder=4)
        ax.scatter([px[k]], [py[k]], color="#f0b90b", s=18, zorder=4)
    ax.axhline(s["hl"], color="#f0b90b", linestyle=":", linewidth=1)
    ax.set_xlim(-1, len(d) + 1)
    ticks = list(range(0, len(d), max(1, len(d) // 6)))
    ax.set_xticks(ticks)
    ax.set_xticklabels([d.index[t].strftime("%d/%m %H:%M") for t in ticks], color=text, fontsize=8)
    ax.tick_params(colors=text, labelsize=8)
    ax.yaxis.tick_right()
    ax.grid(color=grid, linewidth=0.6)
    for sp in ax.spines.values():
        sp.set_color(grid)
    name = s["tv"].split(":")[-1]
    ax.set_title(f"{name} · {s['tf']} · trendomkeer (LH gebroken + HL)",
                 color=text, fontsize=12, loc="left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=bg)
    plt.close(fig)
    return buf.getvalue()


def send_alerts(signals: list[dict]) -> None:
    """Per signaal een foto van de chart met de melding als onderschrift (tekst als het mislukt)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("Geen TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ingesteld, alleen geprint.")
        return
    for s in signals:
        caption = format_signal(s)
        try:
            png = render_chart(s)
            r = http.post(f"https://api.telegram.org/bot{token}/sendPhoto", timeout=60,
                          data={"chat_id": chat, "caption": caption, "parse_mode": "HTML"},
                          files={"photo": ("chart.png", png, "image/png")})
            if r.status_code == 429:  # te snel: wachten en opnieuw
                time.sleep(int(r.json().get("parameters", {}).get("retry_after", 5)) + 1)
                r = http.post(f"https://api.telegram.org/bot{token}/sendPhoto", timeout=60,
                              data={"chat_id": chat, "caption": caption, "parse_mode": "HTML"},
                              files={"photo": ("chart.png", png, "image/png")})
            if not r.ok:
                raise RuntimeError(f"{r.status_code} {r.text[:200]}")
        except Exception as e:
            log(f"Foto voor {s['name']} mislukt ({e}), tekst gestuurd")
            send_telegram([caption])
        time.sleep(1.1)


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


SNAPSHOT_PHOTOS = int(os.environ.get("SNAPSHOT_PHOTOS") or 40)


def snapshot() -> int:
    """Alle tickers die NU aan de voorwaarden voldoen (HH + HL, HL niet gebroken, laatste top
    is de HH), ongeacht hoe oud het signaal is. Stuurt een overzicht + foto's (hoogste volume eerst)."""
    global SIGNAL_MAX_AGE
    SIGNAL_MAX_AGE = 6  # alleen verse omkeringen (HL bevestigd in de laatste 6 candles)
    state = load_state()
    now_et = datetime.now(timezone.utc).astimezone(ET).replace(tzinfo=None)
    signals: list[dict] = []
    for name, fn in (("crypto", scan_crypto), ("goud", lambda: scan_gold(now_et)),
                     ("stocks", lambda: scan_stocks_intraday(state, now_et))):
        try:
            got = fn()
            log(f"Momentopname {name}: {len(got)} voldoen")
            signals += got
        except Exception as e:
            log(f"Momentopname {name} mislukt: {e}")
    signals = [s for s in signals if s.get("vol", 0.0) >= MIN_DOLLAR_VOLUME]
    signals.sort(key=lambda s: s.get("vol", 0.0), reverse=True)
    for s in signals:
        log(format_signal(s).replace("\n", " | "))
    tfs = ", ".join(PIVOT_LEN)
    head = (f"📸 <b>Momentopname</b> ({tfs}): {len(signals)} tickers met een verse trendomkeer "
            f"(LH gebroken + HL, HL nog niet gebroken). Foto's van de {min(len(signals), SNAPSHOT_PHOTOS)} "
            f"met het hoogste volume:")
    send_telegram([head])
    send_alerts(signals[:SNAPSHOT_PHOTOS])
    rest = signals[SNAPSHOT_PHOTOS:]
    if rest:
        send_telegram(["Ook nog: " + ", ".join(f"<code>{s['tv'].split(':')[-1]}</code>" for s in rest)])
    return 0


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
    if os.environ.get("SNAPSHOT", "").strip() in ("1", "true", "ja", "yes"):
        return snapshot()
    if os.environ.get("QUALITY_REPORT", "").strip() in ("1", "true", "ja", "yes"):
        report = quality_report(load_state())
        for r in report:
            log(r.replace("\n", " | "))
        send_telegram(["📊 <b>Chart-kwaliteit</b>"] + report)
        return 0
    if os.environ.get("HALAL_CHECK", "").strip():
        report = halal_check_report(re.split(r"[,\s]+", os.environ["HALAL_CHECK"]))
        for r in report:
            log(r.replace("\n", " | "))
        send_telegram(["☪️ <b>Halal-controle</b> (AAOIFI: max 30% van beurswaarde · MSCI: max 33% van activa)"]
                      + report)
        return 0

    state = load_state()
    state.setdefault("alerts", {})
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET).replace(tzinfo=None)  # naive New York-tijd
    weekday = now_et.weekday() < 5
    minutes = now_et.hour * 60 + now_et.minute
    signals: list[dict] = []
    log("Timeframes: " + ", ".join(f"{tf} ({n} candles)" for tf, n in PIVOT_LEN.items()))
    log(f"Minimum dagvolume: crypto/goud {fmt_vol(MIN_DOLLAR_VOLUME)}, stocks {fmt_vol(MIN_STOCK_VOLUME)}"
        f" · halal-filter stocks: {HALAL_METHOD if HALAL_ONLY else 'uit'}"
        f" · HH minstens {MIN_HH_PCT:g}% boven vorige top")

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
    liquid = state.get("liquid") or {}
    needs_universe = not liquid or state.get("universe_cfg") != universe_cfg()
    if (after_close and state.get("stocks_daily_done") != today) or needs_universe:
        try:
            signals += scan_stocks_daily(state, now_et)
            if after_close:
                state["stocks_daily_done"] = today
        except Exception as e:
            log(f"Stocks daily scan mislukt: {e}")

    # Halal-lijst bijwerken als die verouderd is of het filter aan/uit gezet werd
    halal_stale = HALAL_ONLY and state.get("halal_date", "") < (
        now_utc - timedelta(days=HALAL_REFRESH_DAYS)).date().isoformat()
    if state.get("liquid") and (state.get("halal_on") != HALAL_ONLY or halal_stale):
        apply_halal(state)
    state["halal_on"] = HALAL_ONLY

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
    if new:
        send_alerts(new)

    # Oude meldingen (> 60 dagen) opruimen
    cutoff = (now_utc - timedelta(days=60)).isoformat()
    state["alerts"] = {k: v for k, v in state["alerts"].items() if v >= cutoff[:10]}
    STATE_FILE.write_text(json.dumps(state, indent=0, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
