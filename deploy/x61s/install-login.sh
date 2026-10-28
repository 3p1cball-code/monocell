#!/bin/sh
# Set up a real text login on the X61s: getty on tty1-3, fingerprint via PAM,
# monocell autostart on tty1 after login, reboot/shutdown permissions and the
# Tailscale operator for the connection tool.
#
# Run as root on the target, after install-vt.sh:
#   ./install-login.sh --user ariedel [--no-apt]
set -eu
USER_NAME=${SUDO_USER:-}
NO_APT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --user) USER_NAME=$2; shift 2 ;;
        --no-apt) NO_APT=1; shift ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -n "$USER_NAME" ] || { echo "use --user NAME" >&2; exit 1; }
id "$USER_NAME" >/dev/null 2>&1 || { echo "user $USER_NAME does not exist" >&2; exit 1; }
[ -x /usr/local/bin/monocell ] || { echo "run deploy/x61s/install-vt.sh first" >&2; exit 1; }
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
SHELL_PATH=$(getent passwd "$USER_NAME" | cut -d: -f7)
GROUP_NAME=$(id -gn "$USER_NAME")

# ---------------------------------------------------------------- target/gettys
cat > /etc/systemd/system/monocell.target <<'UNIT'
[Unit]
Description=text console login (monocell on tty1)
Requires=multi-user.target
After=multi-user.target
Wants=getty@tty1.service
Conflicts=monocell@1.service monocell@2.service
AllowIsolate=yes
UNIT
systemctl daemon-reload
systemctl enable getty@tty1.service getty@tty2.service getty@tty3.service >/dev/null 2>&1 || true
# no login-less sessions by default (the running one is left alone)
systemctl disable monocell@1.service monocell@2.service >/dev/null 2>&1 || true
systemctl stop monocell@2.service >/dev/null 2>&1 || true

# ---------------------------------------------------------------- profile block
case "$SHELL_PATH" in
    */zsh)  PROFILE="$HOME_DIR/.zprofile" ;;
    */bash) if [ -f "$HOME_DIR/.bash_profile" ]; then PROFILE="$HOME_DIR/.bash_profile"; else PROFILE="$HOME_DIR/.profile"; fi ;;
    */sh|*/dash) PROFILE="$HOME_DIR/.profile" ;;
    *) PROFILE="" ;;
esac
if [ -n "$PROFILE" ]; then
    [ -f "$PROFILE" ] || : > "$PROFILE"
    [ -f "$PROFILE.bak-monocell" ] || cp -a "$PROFILE" "$PROFILE.bak-monocell"
    python3 - "$PROFILE" <<'PY'
import sys
p = sys.argv[1]
block = '''# >>> monocell console >>>
# added by deploy/x61s/install-login.sh - remove this block for a plain shell login
if [ -z "${SSH_CONNECTION:-}" ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ] \\
   && [ -z "${MONOCELL_NO_AUTOSTART:-}" ]; then
    if [ "$(tty 2>/dev/null)" = "/dev/tty1" ] && command -v monocell >/dev/null 2>&1; then
        TERM=linux exec monocell --lowmem
    fi
fi
# <<< monocell console <<<
'''
s = open(p).read()
start = s.find('# >>> monocell console >>>')
end = s.find('# <<< monocell console <<<')
if start >= 0 and end >= 0:
    s = s[:start] + s[end + len('# <<< monocell console <<<') + 1:]
open(p, 'w').write(s.rstrip('\n') + '\n\n' + block)
PY
    chown "$USER_NAME:$GROUP_NAME" "$PROFILE"
    echo "autostart: $PROFILE (tty1 -> monocell, tty2/3 -> shell)"
else
    echo "warning: unknown shell $SHELL_PATH; add the monocell block manually" >&2
fi

# ---------------------------------------------------------------- fingerprint
if [ "$NO_APT" = 0 ] && ! dpkg -s libpam-fprintd >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y fprintd libpam-fprintd
fi
if command -v fprintd >/dev/null 2>&1 && ! grep -q pam_fprintd /etc/pam.d/common-auth; then
    DEBIAN_FRONTEND=noninteractive pam-auth-update --enable fprintd --force >/dev/null 2>&1 || true
fi
FP=0
if command -v fprintd-list >/dev/null 2>&1; then
    FP=$(fprintd-list "$USER_NAME" 2>/dev/null | grep -c '^ - #' || true)
fi
if [ "${FP:-0}" -gt 0 ]; then
    echo "fingerprint: $FP print(s) enrolled; PAM tries it before the password"
else
    echo "fingerprint: none enrolled - run: fprintd-enroll $USER_NAME"
fi

# ---------------------------------------------------------------- tailscale
if command -v tailscale >/dev/null 2>&1; then
    systemctl is-active tailscaled >/dev/null 2>&1 || systemctl start tailscaled >/dev/null 2>&1 || true
    if tailscale set --operator="$USER_NAME" >/dev/null 2>&1; then
        echo "tailscale: operator set to $USER_NAME (the Tailscale app can connect/disconnect)"
    else
        echo "tailscale: could not set the operator (CLI too old?)" >&2
    fi
fi

# ---------------------------------------------------------------- power actions
if printf '%s' "$USER_NAME" | grep -q '^[A-Za-z0-9_-][A-Za-z0-9_.-]*$'; then
    cat > /etc/polkit-1/rules.d/49-monocell-power.rules <<RULE
// allow the console user to reboot/shut down from monocell
polkit.addRule(function(action, subject) {
    if (subject.user == "$USER_NAME" &&
        ["org.freedesktop.login1.reboot", "org.freedesktop.login1.poweroff",
         "org.freedesktop.login1.reboot-multiple-sessions",
         "org.freedesktop.login1.poweroff-multiple-sessions"].indexOf(action.id) >= 0) {
        return polkit.Result.YES;
    }
});
RULE
    echo "power: reboot/shutdown allowed for $USER_NAME (polkit rule installed)"
fi

# ---------------------------------------------------------------- removable media
if printf '%s' "$USER_NAME" | grep -q '^[A-Za-z0-9_-][A-Za-z0-9_.-]*$'; then
    cat > /etc/polkit-1/rules.d/50-monocell-media.rules <<RULE
// monocell mounts removable media (USB, SD, CF/Microdrive): on the VT there is
// no polkit authentication agent, so the console user is allowed directly.
polkit.addRule(function(action, subject) {
    if (subject.user == "$USER_NAME" &&
        ["org.freedesktop.udisks2.filesystem-mount",
         "org.freedesktop.udisks2.eject-media"].indexOf(action.id) >= 0) {
        return polkit.Result.YES;
    }
});
RULE
    chmod 644 /etc/polkit-1/rules.d/50-monocell-media.rules
    echo "media: mounting removable drives allowed for $USER_NAME (udisks2 + polkit rule)"
fi

# ---------------------------------------------------------------- console mouse
systemctl disable --now gpm.service >/dev/null 2>&1 || true

echo
echo "Text login is ready: reboot, then log in on tty1 (fingerprint or password)."
echo "monocell starts after login; tty2/tty3 give a plain shell. Exiting monocell logs out."
echo "The normal graphical GRUB entry is unchanged."
