#!/bin/sh
# Monocell extras on the X61s - run once as root:
#
#   sudo ~/.local/share/monocell/deploy/x61s/install-extras.sh
#
#  1. console font: repairs the '=' of /usr/share/consolefonts/Lat15-3270.psf.gz
#     (the copy console-setup loads at boot, i.e. the login prompt)
#  2. login screen: /etc/issue with the MONOCELL wordmark in the theme colours
#  3. updates: /usr/local/sbin/monocell-apt + a sudoers rule that allows exactly
#     "monocell-apt update" and "monocell-apt upgrade" without a password, for
#     the Updates app (System -> Updates)
#
# Options: --user NAME (default: the sudo user)  --issue-only (after a theme
# change)  --uninstall (restore the originals)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
USER_NAME=${SUDO_USER:-}
MODE=install
while [ $# -gt 0 ]; do
    case "$1" in
        --user) USER_NAME=$2; shift 2 ;;
        --issue-only) MODE=issue; shift ;;
        --uninstall) MODE=uninstall; shift ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done
[ "$(id -u)" = 0 ] || { echo "run as root (sudo $0)" >&2; exit 1; }
[ -n "$USER_NAME" ] || { echo "use --user NAME" >&2; exit 1; }
id "$USER_NAME" >/dev/null 2>&1 || { echo "user $USER_NAME does not exist" >&2; exit 1; }
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
FONT=/usr/share/consolefonts/Lat15-3270.psf.gz
HELPER=/usr/local/sbin/monocell-apt
SUDOERS=/etc/sudoers.d/monocell-apt

if [ "$MODE" = uninstall ]; then
    [ -f "$FONT.monocell-orig" ] && mv -f "$FONT.monocell-orig" "$FONT" && echo "font restored"
    [ -f /etc/issue.monocell-orig ] && mv -f /etc/issue.monocell-orig /etc/issue && echo "issue restored"
    rm -f "$SUDOERS" "$HELPER" && echo "update helper removed"
    exit 0
fi

make_issue() {
    tmp=$(mktemp)
    chown "$USER_NAME" "$tmp"
    # built as the user: Monocell reads the theme from the user's config
    su -s /bin/sh "$USER_NAME" -c "python3 '$ROOT/tools/make_issue.py' \
        --config '$HOME_DIR/.config/monocell/config.json' --out '$tmp'"
    [ -f /etc/issue.monocell-orig ] || cp -p /etc/issue /etc/issue.monocell-orig
    install -o root -g root -m 0644 "$tmp" /etc/issue
    rm -f "$tmp"
    echo "login screen: /etc/issue (original: /etc/issue.monocell-orig)"
}

if [ "$MODE" = issue ]; then
    make_issue
    exit 0
fi

# 1. console font ------------------------------------------------------------
if [ -f "$FONT" ]; then
    [ -f "$FONT.monocell-orig" ] || cp -p "$FONT" "$FONT.monocell-orig"
    if python3 "$ROOT/tools/fix_psf_equals.py" "$FONT" --check >/dev/null; then
        echo "font: '=' already fine in $FONT"
    else
        python3 "$ROOT/tools/fix_psf_equals.py" "$FONT"
    fi
fi

# 2. login screen ------------------------------------------------------------
make_issue

# 3. update helper -------------------------------------------------------------
install -o root -g root -m 0755 "$HERE/monocell-apt" "$HELPER"
tmp=$(mktemp)
printf '# Monocell Updates app: refresh lists and install upgrades, nothing else\n' > "$tmp"
printf '%s ALL=(root) NOPASSWD: %s update, %s upgrade\n' "$USER_NAME" "$HELPER" "$HELPER" >> "$tmp"
if visudo -cf "$tmp" >/dev/null; then
    install -o root -g root -m 0440 "$tmp" "$SUDOERS"
    echo "updates: $HELPER + $SUDOERS"
else
    echo "sudoers rule failed the visudo check - not installed" >&2
fi
rm -f "$tmp"
echo "done. The login screen shows at the next login prompt (log out or reboot)."
