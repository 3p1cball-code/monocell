#!/bin/sh
# KDE Connect on a text console: headless Xvfb + kdeconnectd, so the phone can
# exchange the clipboard with Monocell through xclip.
#
# Run as root on the target:
#   sudo deploy/x61s/install-kdeconnect.sh --user ariedel [--display :99] [--no-apt]
set -eu
USER_NAME=${SUDO_USER:-}
DISPLAY_NUM=:99
NO_APT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --user) USER_NAME=$2; shift 2 ;;
        --display) DISPLAY_NUM=$2; shift 2 ;;
        --no-apt) NO_APT=1; shift ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -n "$USER_NAME" ] || { echo "use --user NAME" >&2; exit 1; }
id "$USER_NAME" >/dev/null 2>&1 || { echo "user $USER_NAME does not exist" >&2; exit 1; }
UID_N=$(id -u "$USER_NAME")
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
USER_UNIT="$HOME_DIR/.config/systemd/user"

if [ "$NO_APT" = 0 ]; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y kdeconnect xvfb xclip
fi
for c in Xvfb kdeconnectd xclip; do
    command -v "$c" >/dev/null 2>&1 || { echo "$c is missing (apt install kdeconnect xvfb xclip)" >&2; exit 1; }
done

mkdir -p "$USER_UNIT"
cat > "$USER_UNIT/monocell-xvfb.service" <<UNIT
[Unit]
Description=Headless X display for KDE Connect (Monocell)

[Service]
ExecStart=/usr/bin/Xvfb $DISPLAY_NUM -screen 0 800x600x24 -nolisten tcp -noreset
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
UNIT

cat > "$USER_UNIT/monocell-kdeconnect.service" <<UNIT
[Unit]
Description=KDE Connect daemon on $DISPLAY_NUM (Monocell)
After=monocell-xvfb.service
Requires=monocell-xvfb.service

[Service]
Environment=DISPLAY=$DISPLAY_NUM
Environment=QT_QPA_PLATFORM=xcb
Environment=XDG_RUNTIME_DIR=/run/user/$UID_N
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID_N/bus
ExecStart=/usr/bin/kdeconnectd
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNIT
chown -R "$USER_NAME:$(id -gn "$USER_NAME")" "$HOME_DIR/.config/systemd" "$USER_UNIT"

# run the user services now and after every boot, even without a login
loginctl enable-linger "$USER_NAME" >/dev/null 2>&1 || true
systemctl start "user@$UID_N.service" >/dev/null 2>&1 || true
su -s /bin/sh "$USER_NAME" -c \
    "XDG_RUNTIME_DIR=/run/user/$UID_N systemctl --user daemon-reload" || true
su -s /bin/sh "$USER_NAME" -c \
    "XDG_RUNTIME_DIR=/run/user/$UID_N systemctl --user enable --now monocell-xvfb.service monocell-kdeconnect.service" || {
        echo "could not start the user services; check: systemctl --user status monocell-kdeconnect" >&2
        exit 1
    }

sleep 2
if su -s /bin/sh "$USER_NAME" -c "DISPLAY=$DISPLAY_NUM xclip -selection clipboard -o" >/dev/null 2>&1; then
    echo "clipboard: headless display $DISPLAY_NUM is up (empty clipboard is fine)"
else
    echo "clipboard: $DISPLAY_NUM is running; it will hold text after the first sync"
fi
echo
echo "KDE Connect is running headless on $DISPLAY_NUM."
echo "1. Open Monocell -> KDE Connect and press P to pair with the phone (accept there)."
echo "2. Enable the Clipboard plugin for this device in KDE Connect on the phone."
echo "3. In Monocell: V pulls the phone clipboard, C sends Monocell's clipboard."
