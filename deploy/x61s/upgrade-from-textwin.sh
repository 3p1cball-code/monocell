#!/bin/sh
# Upgrade a textwin installation (the old name) to monocell on the X61s.
# Removes the old units, launchers, GRUB entry and autostart block, then runs the
# normal monocell system install.
#
# Run as root on the target, from a checkout of the new tree:
#   sudo deploy/x61s/upgrade-from-textwin.sh --user ariedel [--no-apt] [--keep-old] [--no-grub]
set -eu
USER_NAME=${SUDO_USER:-}
NO_APT=0
KEEP_OLD=0
DO_GRUB=1
while [ $# -gt 0 ]; do
    case "$1" in
        --user) USER_NAME=$2; shift 2 ;;
        --no-apt) NO_APT=1; shift ;;
        --keep-old) KEEP_OLD=1; shift ;;
        --no-grub) DO_GRUB=0; shift ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -n "$USER_NAME" ] || { echo "use --user NAME" >&2; exit 1; }
id "$USER_NAME" >/dev/null 2>&1 || { echo "user $USER_NAME does not exist" >&2; exit 1; }
SRC=$(cd "$(dirname "$0")/../.." && pwd)
[ -f "$SRC/run.sh" ] && [ -d "$SRC/monocell" ] || { echo "not a monocell checkout: $SRC" >&2; exit 1; }

echo "--- stopping the old textwin units"
systemctl disable --now textwin@1.service textwin@2.service >/dev/null 2>&1 || true
systemctl stop textwin@1.service textwin@2.service >/dev/null 2>&1 || true

echo "--- removing old system files"
rm -f /etc/systemd/system/textwin@.service /etc/systemd/system/textwin.target
rm -rf /etc/systemd/system/textwin@*.service.d
rm -f /usr/local/bin/textwin /usr/local/bin/textwin-session
rm -f /etc/polkit-1/rules.d/49-textwin-power.rules
rm -f /etc/grub.d/40_textwin
systemctl daemon-reload

# ---------------------------------------------------------------- user profile
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
for PROFILE in "$HOME_DIR/.profile" "$HOME_DIR/.bash_profile"; do
    [ -f "$PROFILE" ] || continue
    python3 - "$PROFILE" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
a, b = '# >>> textwin console >>>', '# <<< textwin console <<<'
i, j = s.find(a), s.find(b)
if i >= 0 and j >= 0:
    open(p, 'w').write(s[:i] + s[j + len(b) + 1:])
    print('removed old autostart block from', p)
PY
    chown "$USER_NAME" "$(id -gn "$USER_NAME")" "$PROFILE"
done

echo "--- installing monocell (system copy)"
if [ "$NO_APT" = 1 ]; then
    "$SRC/deploy/x61s/install-vt.sh" --user "$USER_NAME" --no-apt
    "$SRC/deploy/x61s/install-login.sh" --user "$USER_NAME" --no-apt
else
    "$SRC/deploy/x61s/install-vt.sh" --user "$USER_NAME"
    "$SRC/deploy/x61s/install-login.sh" --user "$USER_NAME"
fi

if [ "$DO_GRUB" = 1 ]; then
    echo "--- rewriting the alternate GRUB entry"
    "$SRC/deploy/x61s/install-grub-entry.sh"
fi

if [ "$KEEP_OLD" = 0 ]; then
    echo "--- removing the old program copy"
    rm -rf /opt/textwin
fi

echo
echo "Upgrade complete. The text login now starts monocell; tty2/3 stay plain shells."
echo "The old GRUB entry is gone. Reboot and pick \"monocell (text console)\" if you want"
echo "the text-login boot, otherwise the normal entry is unchanged."
