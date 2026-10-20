# HH + HL trend scanner

Scant elk half uur **alle US stocks** (prijs > $5, gem. volume > 500k), **alle crypto USDT-paren op Binance** en **goud** (COMEX GC1!) op de timeframes die jij kiest (standaard **4h en daily**), en stuurt een Telegram-melding zodra een ticker een nieuwe uptrend maakt: **Higher High + Higher Low**.

## Minimum volume

Je krijgt alleen meldingen voor tickers met minstens **$5M dagvolume** (mediaan over de laatste 20 dagen, zodat pump-dagen niet meetellen). Aanpassen: maak een variabele `MIN_VOLUME` aan (zelfde plek als hieronder), bv. `25m`, `500k` of `1b`.

## Stocks: volume en halal

- Stocks moeten minstens **$200M dagvolume** hebben (mediaan 20 dagen) — minder wicks en gaps. Aanpassen: variabele `MIN_VOLUME_STOCKS`, bv. `100m`. (`MIN_VOLUME` geldt dan nog voor crypto en goud.)
- **Halal-filter** (standaard aan, uitzetten met variabele `HALAL` = `0`), volgens de MSCI Islamic-methode met officiële SEC-gegevens:
  - uitgesloten activiteiten (SIC-code + lijst): banken, verzekeraars en andere financiële diensten, alcohol, tabak, varkensvlees, wapens, hotels/casino's, gokken, film/entertainment;
  - schulden < 33% en cash + kortlopende beleggingen < 33% van de totale activa (laatste kwartaalcijfers).
  - Buitenlandse bedrijven zonder US-GAAP-cijfers (bv. ASML, TSM) worden overgeslagen omdat ze niet gecontroleerd kunnen worden.
  - Niet gecontroleerd: het aandeel verboden inkomsten (< 5%). Controleer een aandeel bij twijfel in een app als Zoya of Musaffa.
  - Balanscijfers worden wekelijks vernieuwd, sectorcodes maandelijks.

## Minimum hoogte van de HH

Een nieuwe top telt pas als Higher High als hij minstens **1%** boven de vorige top ligt; minder is eerder een dubbele top. Aanpassen: variabele `MIN_HH_PCT`, bv. `0.5` of `2`.

## Timeframes aanpassen

GitHub → repo → *Settings* → *Secrets and variables* → *Actions* → tabblad **Variables** → *New repository variable*:

- Name: `TIMEFRAMES`
- Value: timeframes met het aantal bevestigingscandles, gescheiden door komma's, bv. `4h:2,1d:2`

Mogelijke timeframes: `1h`, `2h`, `4h`, `1d`, `1w`. Het getal na `:` is hoeveel candles links en rechts van een top/bodem nodig zijn (minder = sneller, meer = betrouwbaarder). Zonder variabele geldt `4h:2,1d:2`. De wijziging geldt vanaf de volgende run.

- Crypto: Binance-data = exact dezelfde candles als `BINANCE:` op TradingView.
- Stocks/goud: Yahoo Finance, 4h-candles opgebouwd zoals TradingView (9:30–13:30 en 13:30–16:00 New York-tijd).
- Stocks 4h: tijdens beurstijden. Stocks daily + filter: één keer per dag na de slotbel.
- Melding bij elke nieuwe bevestigde Higher High die op een Higher Low volgt (L1 → H1 → L2 → H2, met H2 > H1 en L2 > L1). L2 = laagste punt tussen H1 en H2.
- Meldingen staan gesorteerd op dagvolume in dollar, hoogste eerst.
- Tik op de ticker om hem te kopiëren (plakken in de TradingView-zoekbalk), of tik op *Chart* om de chart te openen.

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
