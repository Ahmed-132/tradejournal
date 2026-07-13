#!/usr/bin/env bash
# Bouwt de app en installeert 'm op je verbonden iPhone.
# Gebruik:  ./install-iphone.sh
# Vereist:  iPhone via kabel verbonden + ontgrendeld, Apple ID in Xcode, Ontwikkelaarsmodus aan.
set -euo pipefail

TEAM="JHQZ8N93RR"                         # Apple Developer team (Ahmed Hamamine)
BUNDLE="be.hamamin.tradejournal"
BUILD="/tmp/tj-build"

cd "$(dirname "$0")"

echo "▸ Web-app bundelen…"
npm run build:www
npx cap sync ios >/dev/null

echo "▸ iPhone zoeken…"
UDID=$(xcrun devicectl list devices --json-output /tmp/_devs.json >/dev/null 2>&1 && \
  python3 -c "import json;d=json.load(open('/tmp/_devs.json'));print(next(x['hardwareProperties']['udid'] for x in d['result']['devices']))")
echo "  iPhone UDID: $UDID"

echo "▸ Bouwen en ondertekenen…"
rm -rf "$BUILD"
xcodebuild -project ios/App/App.xcodeproj -scheme App -configuration Debug \
  -destination 'generic/platform=iOS' -derivedDataPath "$BUILD" \
  -allowProvisioningUpdates DEVELOPMENT_TEAM="$TEAM" CODE_SIGN_STYLE=Automatic \
  clean build

echo "▸ Installeren op iPhone…"
xcrun devicectl device install app --device "$UDID" "$BUILD/Build/Products/Debug-iphoneos/App.app"

echo "✓ Klaar — TradeJournal staat bijgewerkt op je iPhone."
