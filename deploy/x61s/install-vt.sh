#!/bin/sh
# Install monocell as an extra console frontend on Debian (e.g. the X61s).
# Run on the target, as root:  sudo ./install-vt.sh [options]
#
#   --user NAME        desktop user that runs monocell (default: $SUDO_USER)
#   --tty N            virtual console number (default: 2)
#   --prefix PATH      install path (default: /opt/monocell)
#   --no-desktop       boot to multi-user.target and disable the display manager
#   --no-apt           do not install packages
#   --no-systemd       only copy files / write the launchers (for testing)
#   --uninstall        remove the service and launchers (keeps --prefix)
set -eu

PREFIX=/opt/monocell
TTY=2
DO_APT=1
DO_SYSTEMD=1
NO_DESKTOP=0
UNINSTALL=0
KIOSK=0
USER_NAME=${SUDO_USER:-}
while [ $# -gt 0 ]; do
    case "$1" in
        --user) USER_NAME=$2; shift 2 ;;
        --tty) TTY=$2; shift 2 ;;
        --prefix) PREFIX=$2; shift 2 ;;
        --no-desktop) NO_DESKTOP=1; shift ;;
        --no-apt) DO_APT=0; shift ;;
        --no-systemd) DO_SYSTEMD=0; shift ;;
        --kiosk) KIOSK=1; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done
USER_NAME=${USER_NAME:-$(id -un)}

if [ "$UNINSTALL" = 1 ]; then
    [ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
    systemctl disable --now "monocell@$TTY.service" 2>/dev/null || true
    rm -f /etc/systemd/system/monocell@.service
    systemctl daemon-reload
    rm -f /usr/local/bin/monocell /usr/local/bin/monocell-session
    echo "monocell@ removed (files in $PREFIX kept)"
    exit 0
fi

if [ "$DO_SYSTEMD" = 1 ] && [ "$(id -u)" != 0 ]; then
    echo "run as root (or use --no-systemd for a test install)" >&2
    exit 1
fi
if [ "$(id -u)" = 0 ] && ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "user '$USER_NAME' does not exist (use --user)" >&2
    exit 1
fi

SRC=$(cd "$(dirname "$0")/../.." && pwd)

if [ "$DO_APT" = 1 ] && [ "$(id -u)" = 0 ]; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pil ffmpeg poppler-utils yt-dlp curl
fi

# YouTube needs a current yt-dlp; the distro package is often too old
if [ "$(id -u)" = 0 ] && command -v curl >/dev/null 2>&1; then
    if curl -fsSL -o /usr/local/bin/yt-dlp.new \
        https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp 2>/dev/null; then
        chmod +x /usr/local/bin/yt-dlp.new
        mv /usr/local/bin/yt-dlp.new /usr/local/bin/yt-dlp
        echo "yt-dlp: $(/usr/local/bin/yt-dlp --version 2>/dev/null) (latest binary)"
    else
        echo "yt-dlp: could not download the latest binary; using the packaged version" >&2
    fi
fi

mkdir -p "$PREFIX"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude '.git' --exclude '.shots' --exclude '.testhome' --exclude '.freshhome' \
        --exclude '.demohome' --exclude '.jshome' --exclude '.matrixhome' --exclude '.shadehome' \
        --exclude '.icshome' --exclude 'tmpaudio' --exclude '__pycache__' \
        "$SRC/" "$PREFIX/"
else
    cp -r "$SRC/." "$PREFIX/"
    rm -rf "$PREFIX/.git" "$PREFIX/.shots" "$PREFIX/.testhome" "$PREFIX/tmpaudio"
fi

BIN=/usr/local/bin
[ "$DO_SYSTEMD" = 1 ] || BIN="$PREFIX/bin"
mkdir -p "$BIN"
sed "s|@PREFIX@|$PREFIX|g" > "$BIN/monocell" <<'LAUNCH'
#!/bin/sh
PYTHONPATH="@PREFIX@${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m monocell "$@"
LAUNCH
chmod +x "$BIN/monocell"

sed "s|@USER@|$USER_NAME|g; s|@BIN@|$BIN|g" > "$BIN/monocell-session" <<'SESSION'
#!/bin/sh
# Shell fallback after leaving monocell on the console.
while :; do
    "@BIN@/monocell" --lowmem
    echo
    echo "monocell beendet. Shell (exit startet monocell neu)."
    /bin/bash -l
done
SESSION
chmod +x "$BIN/monocell-session"

if [ "$DO_SYSTEMD" = 1 ]; then
    cat > /etc/systemd/system/monocell@.service <<UNIT
[Unit]
Description=monocell console on tty%i
After=systemd-user-sessions.service
Conflicts=getty@tty%i.service
Before=getty@tty%i.service

[Service]
User=$USER_NAME
Environment=TERM=linux
TTYPath=/dev/tty%i
TTYReset=yes
TTYVHangup=yes
StandardInput=tty
StandardOutput=tty
ExecStartPre=+/bin/sh -c 'command -v kbd_mode >/dev/null 2>&1 && kbd_mode -u || true'
ExecStart=$BIN/monocell-session
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT
    # do not start this instance on the alternate monocell.target boot (that boot runs tty1)
    mkdir -p "/etc/systemd/system/monocell@$TTY.service.d"
    cat > "/etc/systemd/system/monocell@$TTY.service.d/no-alt.conf" <<ALT
[Unit]
ConditionKernelCommandLine=!systemd.unit=monocell.target
ALT
    systemctl daemon-reload
    if [ "$KIOSK" = 1 ]; then
        # do not start this instance on the alternate monocell.target boot (that boot runs tty1)
        mkdir -p "/etc/systemd/system/monocell@$TTY.service.d"
        cat > "/etc/systemd/system/monocell@$TTY.service.d/no-alt.conf" <<ALT
[Unit]
ConditionKernelCommandLine=!systemd.unit=monocell.target
ALT
        systemctl daemon-reload
        systemctl enable --now "monocell@$TTY.service"
        echo "kiosk service monocell@$TTY.service enabled - switch with Ctrl+Alt+F$TTY"
    else
        # login mode (default): no login-less session; install-login.sh sets up getty + PAM
        systemctl disable monocell@1.service monocell@2.service >/dev/null 2>&1 || true
        systemctl stop monocell@2.service >/dev/null 2>&1 || true
        echo "login mode: kiosk service not enabled (use --kiosk for a login-less console)"
        echo "now run: deploy/x61s/install-login.sh --user $USER_NAME"
    fi
    # gpm draws a second mouse cursor on the console; monocell has its own (/dev/input/mice)
    if systemctl list-unit-files gpm.service >/dev/null 2>&1; then
        systemctl disable --now gpm.service >/dev/null 2>&1 || true
    fi
    usermod -aG input "$USER_NAME" 2>/dev/null || true
fi

if [ "$NO_DESKTOP" = 1 ] && [ "$(id -u)" = 0 ]; then
    echo "--- disabling the graphical session (rollback shown below) ---"
    systemctl set-default multi-user.target
    DM=""
    for d in lightdm gdm3 gdm sddm lxdm xdm; do
        if systemctl list-unit-files "$d.service" >/dev/null 2>&1 && \
           systemctl is-enabled "$d.service" >/dev/null 2>&1; then DM="$d"; break; fi
    done
    if [ -n "$DM" ]; then
        systemctl disable --now "$DM.service" || true
    fi
    cat <<ROLLBACK
Boot target: multi-user.target${DM:+ (display manager: $DM disabled)}
Rollback:
  sudo systemctl set-default graphical.target
  ${DM:+sudo systemctl enable --now $DM.service}
  sudo systemctl disable --now monocell@$TTY.service
ROLLBACK
fi

# ---------------------------------------------------------------- backlight
if [ "$(id -u)" = 0 ]; then
    install -m 0644 "$SRC/deploy/x61s/90-monocell-backlight.rules" \
        /etc/udev/rules.d/90-monocell-backlight.rules
    usermod -aG video "$USER_NAME" 2>/dev/null || true
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=backlight --action=add 2>/dev/null || true
    echo "backlight: group video can write brightness (monocell hotkeys and Fn keys)"
fi

echo "installed to $PREFIX; launcher: $BIN/monocell"
if [ "$KIOSK" = 1 ]; then
    echo "try it without rebooting:  sudo systemctl start monocell@$TTY && sudo chvt $TTY"
else
    echo "complete the text login setup with deploy/x61s/install-login.sh"
fi
