# TradeJournal — Live Trading Journal

Een trading journal in de stijl van TradeZella, gericht op **één ding**: het bijhouden en
analyseren van je trades. Werkt volledig lokaal in je browser — geen account, geen server,
geen abonnement. Je data blijft op je eigen computer.

## Functies

- **Dashboard** — netto P&L, winratio, profit factor, verwachting per trade, gemiddelde
  winst/verlies, beste & slechtste trade.
- **Equity-curve** — je cumulatieve resultaat over tijd, met winst/verlies-kleuring.
- **Win/verlies-donut** — visuele verdeling van je uitkomsten.
- **Trades-overzicht** — sorteerbare tabel; toevoegen, bewerken, verwijderen.
- **Kalender** — dagelijkse P&L per maand, groen voor winst, rood voor verlies.
- **Automatische CSV-import** — exporteer bij je broker, sleep het bestand erin; de kolommen
  (datum, symbool, richting, prijs, aantal, kosten, P&L) worden automatisch herkend.
- **CSV-export** — download al je trades als back-up.
- **Installeerbaar als app (PWA)** — draait offline in een eigen venster op Mac, Windows,
  iPhone en Android.

## De app staat live

**➡️ <https://ahmed-132.github.io/tradejournal/>**

Deze link werkt overal en is de app zelf. Elke wijziging die naar de `main`-branch wordt
gepusht, staat na ~1 minuut automatisch live.

### Op je iPhone installeren (werkt daarna altijd + offline)

1. Open de link hierboven in **Safari** (moet Safari zijn).
2. Tik onderaan op de **Deel-knop** (vierkantje met pijl omhoog).
3. Tik op **"Zet op beginscherm"** → **Voeg toe**.
4. Je hebt nu een app-icoon. Het opent in een eigen venster (zonder browserbalk), met een
   onderbalk om te navigeren, en werkt **offline — je Mac hoeft niet aan te staan**.

### Op de computer

Dubbelklik op `index.html` om lokaal te openen, of gebruik gewoon de live-link. In
Chrome/Edge kun je via het **installeer-icoon** (⊕) rechts in de adresbalk de app ook op je
bureaublad installeren.

## Trades invoeren

- **Handmatig:** klik op **+ Trade toevoegen**. Vul minimaal een symbool in. Laat je het
  P&L-veld leeg, dan wordt de winst/verlies automatisch berekend uit entry, exit en aantal
  (rekening houdend met long/short en kosten).
- **Automatisch via CSV:** ga naar **Importeren**, sleep je broker-export erin. Werkt met de
  meeste brokers (MT4/MT5, Interactive Brokers, Binance, Bybit, Webull, Trading212, enz.).
  Klopt een kolom niet? Je kunt de koppeling handmatig aanpassen vóór het importeren.
  Probeer het met het meegeleverde `voorbeeld-trades.csv`.

Geen data bij de hand? Klik op **Importeren → Voorbeelddata laden** om de app met 60
voorbeeldtrades te bekijken.

## Over "automatische sync zoals TradeZella"

TradeZella's live broker-sync werkt via betaalde koppelingen (OAuth per broker, API-keys van
diensten zoals SnapTrade). Dat kan deze app niet zelf regelen zonder jouw broker-toegang en
zulke koppelingen. De automatische route hier is daarom **CSV-import**: je exporteert bij je
broker en alle trades + statistieken worden automatisch ingeladen en berekend.

## Bestanden

| Bestand | Doel |
|---|---|
| `index.html` | App-structuur |
| `styles.css` | Vormgeving (donker thema) |
| `app.js` | Alle logica: opslag, statistieken, grafieken, import |
| `manifest.webmanifest`, `sw.js` | Maken de app installeerbaar & offline |
| `voorbeeld-trades.csv` | Test-CSV voor de import |

## Je data

Alles staat lokaal in je browser (`localStorage`). Wis je browsergegevens, dan ben je je
trades kwijt — maak dus af en toe een back-up via **Trades → Exporteer CSV**.
