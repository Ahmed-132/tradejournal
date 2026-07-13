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

## Zo start je

1. **Snelste manier:** dubbelklik op `index.html` — hij opent meteen in je browser.
2. **Als app installeren (aanbevolen):** start een kleine lokale server zodat de app offline
   installeerbaar wordt. Open een Terminal in deze map en voer uit:

   ```bash
   python3 -m http.server 8137
   ```

   Ga daarna in je browser naar <http://localhost:8137>. In Chrome/Edge zie je rechts in de
   adresbalk een **installeer-icoon** (⊕) — klik erop om TradeJournal als losse app te
   installeren. Op iPhone: Safari → Deel-knop → "Zet op beginscherm".

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
