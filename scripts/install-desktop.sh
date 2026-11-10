#!/usr/bin/env bash
# Install nhcatalogue as a desktop application for the current user.
#
#   ./scripts/install-desktop.sh
#
# Creates an application-menu entry and installs the icon into the user's
# hicolor theme. The entry launches the existing virtualenv install, so the
# repo and .venv must stay where they are.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="nhcatalogue"
BIN="$ROOT/.venv/bin/nhcatalogue"
ICON_SRC="$ROOT/assets/nhcatalogue.svg"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
ICON_ROOT="$DATA/icons/hicolor"
APP_DIR="$DATA/applications"

if [[ ! -x "$BIN" ]]; then
  echo "error: $BIN not found." >&2
  echo "Create the virtualenv and install first: python3 -m venv .venv && .venv/bin/pip install -e '.[desktop]'" >&2
  exit 1
fi
if [[ ! -f "$ICON_SRC" ]]; then
  echo "error: icon not found at $ICON_SRC" >&2
  exit 1
fi

mkdir -p "$APP_DIR" "$ICON_ROOT/scalable/apps"
install -m 0644 "$ICON_SRC" "$ICON_ROOT/scalable/apps/$APP_ID.svg"

if command -v rsvg-convert >/dev/null 2>&1; then
  for size in 32 48 64 128 256; do
    mkdir -p "$ICON_ROOT/${size}x${size}/apps"
    rsvg-convert -w "$size" -h "$size" "$ICON_SRC" -o "$ICON_ROOT/${size}x${size}/apps/$APP_ID.png"
  done
fi

cat >"$APP_DIR/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=nhcatalogue
GenericName=Manga Catalogue
Comment=Browse, catalogue, download and read galleries
Exec="$BIN" gui
Path=$ROOT
Icon=$APP_ID
Terminal=false
Categories=Graphics;Viewer;
Keywords=manga;doujin;catalogue;reader;download;
StartupNotify=true
StartupWMClass=nhcatalogue
EOF
chmod 0644 "$APP_DIR/$APP_ID.desktop"

if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$APP_DIR/$APP_ID.desktop" || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$ICON_ROOT" >/dev/null 2>&1 || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
  kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
  kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi

echo "Installed desktop entry: $APP_DIR/$APP_ID.desktop"
echo "Installed icon:         $ICON_ROOT/scalable/apps/$APP_ID.svg"
echo "Launch it from your application menu (search for \"nhcatalogue\")."
