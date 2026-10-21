#!/bin/sh
# Install monocell for the current user (no root required).
set -e
SRC=$(cd "$(dirname "$0")" && pwd)
PREFIX=${PREFIX:-$HOME/.local}
BIN="$PREFIX/bin"
SHARE="$PREFIX/share/monocell"
mkdir -p "$BIN" "$SHARE" "$PREFIX/share/applications" "$PREFIX/share/icons/hicolor/scalable/apps"
rm -rf "$SHARE/monocell"
cp -r "$SRC/monocell" "$SHARE/"
cat > "$BIN/monocell" <<LAUNCH
#!/bin/sh
PYTHONPATH="$SHARE\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m monocell "\$@"
LAUNCH
chmod +x "$BIN/monocell"
cat > "$PREFIX/share/applications/monocell.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Monocell
Comment=Text-mode desktop environment
Exec=$BIN/monocell
Icon=monocell
Terminal=true
Categories=System;Utility;
Keywords=terminal;desktop;tui;ascii;
DESK
cat > "$PREFIX/share/icons/hicolor/scalable/apps/monocell.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="6" fill="#161616"/>
  <rect x="11" y="7" width="8" height="8" fill="#2c2c2c"/>
  <rect x="43" y="7" width="8" height="8" fill="#2c2c2c"/>
  <rect x="11" y="15" width="8" height="8" fill="#2c2c2c"/>
  <rect x="19" y="15" width="8" height="8" fill="#2c2c2c"/>
  <rect x="35" y="15" width="8" height="8" fill="#2c2c2c"/>
  <rect x="43" y="15" width="8" height="8" fill="#2c2c2c"/>
  <rect x="11" y="23" width="8" height="8" fill="#2c2c2c"/>
  <rect x="27" y="23" width="8" height="8" fill="#2c2c2c"/>
  <rect x="43" y="23" width="8" height="8" fill="#2c2c2c"/>
  <rect x="11" y="31" width="8" height="8" fill="#2c2c2c"/>
  <rect x="27" y="31" width="8" height="8" fill="#2c2c2c"/>
  <rect x="43" y="31" width="8" height="8" fill="#2c2c2c"/>
  <rect x="11" y="39" width="8" height="8" fill="#2c2c2c"/>
  <rect x="43" y="39" width="8" height="8" fill="#2c2c2c"/>
  <rect x="11" y="47" width="8" height="8" fill="#2c2c2c"/>
  <rect x="43" y="47" width="8" height="8" fill="#2c2c2c"/>
  <rect x="11" y="55" width="8" height="8" fill="#2c2c2c"/>
  <rect x="43" y="55" width="8" height="8" fill="#2c2c2c"/>
  <rect x="8" y="4" width="8" height="8" fill="#ffb766"/>
  <rect x="40" y="4" width="8" height="8" fill="#ffb766"/>
  <rect x="8" y="12" width="8" height="8" fill="#ff8a1c"/>
  <rect x="16" y="12" width="8" height="8" fill="#ff8a1c"/>
  <rect x="32" y="12" width="8" height="8" fill="#ff8a1c"/>
  <rect x="40" y="12" width="8" height="8" fill="#ff8a1c"/>
  <rect x="8" y="20" width="8" height="8" fill="#ff8a1c"/>
  <rect x="24" y="20" width="8" height="8" fill="#ff8a1c"/>
  <rect x="40" y="20" width="8" height="8" fill="#ff8a1c"/>
  <rect x="8" y="28" width="8" height="8" fill="#ff8a1c"/>
  <rect x="24" y="28" width="8" height="8" fill="#ff8a1c"/>
  <rect x="40" y="28" width="8" height="8" fill="#ff8a1c"/>
  <rect x="8" y="36" width="8" height="8" fill="#ff8a1c"/>
  <rect x="40" y="36" width="8" height="8" fill="#ff8a1c"/>
  <rect x="8" y="44" width="8" height="8" fill="#ff8a1c"/>
  <rect x="40" y="44" width="8" height="8" fill="#ff8a1c"/>
  <rect x="8" y="52" width="8" height="8" fill="#ff8a1c"/>
  <rect x="40" y="52" width="8" height="8" fill="#ff8a1c"/>
</svg>
SVG
echo "installed: $BIN/monocell"
echo "start from a terminal: monocell   (or from the application menu)"
