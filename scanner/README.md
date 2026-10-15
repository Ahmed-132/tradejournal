# HH + HL trend scanner

Scant elk uur **alle US stocks** (prijs > $5, gem. volume > 500k), **alle crypto USDT-paren op Binance** en **goud** (COMEX GC1!) op **1h, 4h en daily**, en stuurt een Telegram-melding zodra een ticker een nieuwe uptrend maakt: **Higher High + Higher Low**.

| Timeframe | Top/bodem bevestigd na |
|---|---|
| 1h | 3 candles |
| 4h | 2 candles |
| 1D | 2 candles |

- Crypto: Binance-data = exact dezelfde candles als `BINANCE:` op TradingView.
- Stocks/goud: Yahoo Finance, 4h-candles opgebouwd zoals TradingView (9:30–13:30 en 13:30–16:00 New York-tijd).
- Stocks 1h/4h: tijdens beurstijden. Stocks daily + filter: één keer per dag na de slotbel.
- Je krijgt één melding per nieuwe uptrend (niet bij elke nieuwe HH).

## Instellen (eenmalig, ±5 min)

1. **Telegram-bot maken**: open Telegram, zoek `@BotFather`, stuur `/newbot` en volg de stappen. Je krijgt een **token** (zoals `123456:ABC-...`).
2. **Chat-ID vinden**: stuur je nieuwe bot een bericht (bv. "hoi"), open daarna in je browser
   `https://api.telegram.org/bot<TOKEN>/getUpdates` en zoek `"chat":{"id":123456789` — dat getal is je **chat-ID**.
3. **Secrets toevoegen** op GitHub: repo → *Settings* → *Secrets and variables* → *Actions* → *New repository secret*:
   - `TELEGRAM_BOT_TOKEN` = je token
   - `TELEGRAM_CHAT_ID` = je chat-ID
4. **Starten**: repo → *Actions* → *HH-HL scanner* → *Run workflow*. Daarna draait hij vanzelf elk uur.

## Goed om te weten

- Gratis zolang de repo **public** is (onbeperkte Actions-minuten).
- GitHub zet geplande workflows uit na 60 dagen zonder activiteit in de repo; dan krijg je een mail en klik je op *Enable workflow*.
- GitHub start geplande runs soms een paar minuten later dan gepland.
- Instellingen (candles, prijs/volume-filter) staan bovenaan `scanner/scan.py`.
- Lokaal testen: `pip install -r scanner/requirements.txt` en `python scanner/scan.py --dry-run`.
