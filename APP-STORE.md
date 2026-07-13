# App Store-route (Capacitor + Xcode)

De app is verpakt als een **echte native iOS-app** met [Capacitor](https://capacitorjs.com).
Dezelfde web-code (`index.html`, `styles.css`, `app.js`) draait binnenin de native app. Het
Xcode-project staat klaar in de map `ios/`.

> **Belangrijk:** je iPhone-app werkt vandaag al via de PWA-link (zie `README.md`). Deze
> App Store-route is alleen nodig als je de app écht in de Apple App Store wilt publiceren.
> Dat vereist een **Apple Developer-account ($99/jaar)** — dat moet jij zelf aanmaken.

## Wat er al klaar staat

- `package.json` + Capacitor (`@capacitor/core`, `@capacitor/ios`, `@capacitor/cli`)
- `capacitor.config.json` — app-id `be.hamamin.tradejournal`, naam *TradeJournal*
- `ios/` — het volledige, werkende Xcode-project
- Script `npm run build:www` bundelt de web-app naar `www/` en in de app

## Workflow: web-wijziging → in de app

Elke keer dat je de web-app aanpast:

```bash
npm run sync        # kopieert de web-app in de iOS-app en synct Capacitor
npm run ios         # opent het project in Xcode
```

## Stap voor stap naar de App Store

1. **Apple Developer-account** — meld je aan op <https://developer.apple.com/programs/>
   ($99/jaar). Dit is verplicht voor de App Store en heb je nodig voor stap 4+.
2. **Open het project in Xcode:**
   ```bash
   npm run ios
   ```
3. **App-icoon & naam** — in Xcode: selecteer het `App`-target → *General*. Sleep een
   1024×1024 icoon in *App Icons* (te maken uit `assets/icon.svg`). Naam staat al op
   *TradeJournal*.
4. **Ondertekenen (signing):** target `App` → tab *Signing & Capabilities* → vink
   *Automatically manage signing* aan en kies jouw *Team* (verschijnt na stap 1). Xcode maakt
   dan automatisch een provisioning profile.
5. **Testen op je eigen iPhone:** sluit je iPhone met kabel aan (of via wifi), kies hem
   bovenin als doel, druk op ▶︎ (Run). De app installeert op je toestel.
6. **Bouwen voor de store:** menu *Product → Archive*. Als het archief klaar is opent het
   *Organizer*-venster → *Distribute App* → *App Store Connect*.
7. **App Store Connect:** maak op <https://appstoreconnect.apple.com> een nieuwe app aan
   (naam, beschrijving, screenshots, privacybeleid), koppel de geüploade build, en dien in
   voor *Review*. Apple beoordeelt meestal binnen 1–3 dagen.

## Goed om te weten

- **Kosten:** $99/jaar zolang de app in de store staat.
- **Privacybeleid:** Apple vereist een privacy-URL. Omdat deze app alle data lokaal op het
  toestel houdt en niets verstuurt, is dat een korte, simpele tekst.
- **Data lokaal:** ook in de native app staat je data lokaal op de telefoon (WebView
  `localStorage`). Maak back-ups via **Trades → Exporteer CSV**.
- **Updates:** na een wijziging opnieuw `npm run sync`, versienummer ophogen in Xcode
  (*General → Version/Build*), *Archive* en opnieuw uploaden.
