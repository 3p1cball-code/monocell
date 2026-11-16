#!/bin/sh
# Install Monocell as an alternative Nobara (Fedora) frontend: a "MONOCELL - TUI"
# entry in the GRUB menu that boots to a text login on tty1, where monocell starts
# after login. The normal graphical boot (KDE) stays the default and untouched.
#
# Run as root on the target:
#
#   sudo ./install-frontend.sh [--user NAME] [--remove]
#
# What it does:
#   - installs/updates /etc/systemd/system/monocell.target (multi-user + getty@tty1,
#     no graphical.target)
#   - clones the running kernel's BLS entry to /boot/loader/entries/monocell.conf
#     with systemd.unit=monocell.target and the title "MONOCELL - TUI"
#   - makes the GRUB menu visible (timeout/style) and regenerates grub.cfg
#   - adds the user to the video,input groups (console mouse, backlight)
#   - installs the polkit rules for reboot/shutdown and udisks2 mount/eject
#     (there is no polkit agent on the VT) and the backlight udev rule
#   - adds the tty1 autostart block to the user's ~/.bash_profile
#
# Re-run after a kernel update so the menu entry follows the new kernel.
set -eu
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

USER_NAME=${SUDO_USER:-}
if [ -z "$USER_NAME" ] && [ -n "${PKEXEC_UID:-}" ]; then
    USER_NAME=$(getent passwd "$PKEXEC_UID" | cut -d: -f1)
fi
MODE=install
while [ $# -gt 0 ]; do
    case "$1" in
        --user) USER_NAME=$2; shift 2 ;;
        --remove) MODE=remove; shift ;;
        -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done
[ "$(id -u)" = 0 ] || { echo "run as root (sudo $0)" >&2; exit 1; }
if [ -z "$USER_NAME" ]; then
    echo "use --user NAME" >&2
    exit 1
fi
id "$USER_NAME" >/dev/null 2>&1 || { echo "user $USER_NAME does not exist" >&2; exit 1; }
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
SHELL_PATH=$(getent passwd "$USER_NAME" | cut -d: -f7)
GROUP_NAME=$(id -gn "$USER_NAME")
TARGET=/etc/systemd/system/monocell.target
BLS_DIR=/boot/loader/entries
BLS_ENTRY=$BLS_DIR/monocell.conf

profile_block() {
    case "$SHELL_PATH" in
        */zsh)  echo "$HOME_DIR/.zprofile" ;;
        */bash) if [ -f "$HOME_DIR/.bash_profile" ]; then echo "$HOME_DIR/.bash_profile"; else echo "$HOME_DIR/.profile"; fi ;;
        */sh|*/dash) echo "$HOME_DIR/.profile" ;;
        *) echo "" ;;
    esac
}

remove_profile_block() {
    PROFILE=$(profile_block)
    [ -n "$PROFILE" ] && [ -f "$PROFILE" ] || return 0
    python3 - "$PROFILE" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
start = s.find('# >>> monocell console >>>')
end = s.find('# <<< monocell console <<<')
if start >= 0 and end >= 0:
    s = s[:start] + s[end + len('# <<< monocell console <<<') + 1:]
    open(p, 'w').write(s)
PY
}

if [ "$MODE" = remove ]; then
    rm -f "$TARGET" "$BLS_ENTRY"
    rm -f /etc/polkit-1/rules.d/49-monocell-power.rules \
          /etc/polkit-1/rules.d/50-monocell-media.rules
    rm -f /etc/udev/rules.d/90-monocell-backlight.rules
    systemctl daemon-reload 2>/dev/null || true
    udevadm control --reload 2>/dev/null || true
    remove_profile_block
    echo "removed: monocell.target, GRUB entry, polkit/udev rules, autostart block"
    echo "kept: the normal boot target, GRUB menu settings, the user's files"
    echo "note: group memberships (video,input) were left alone"
    exit 0
fi

# ---------------------------------------------------------------- systemd target
cat > "$TARGET" <<'UNIT'
[Unit]
Description=Monocell text console (tty1)
Requires=multi-user.target
After=multi-user.target
Wants=getty@tty1.service
Conflicts=graphical.target
AllowIsolate=yes
UNIT
systemctl daemon-reload
systemctl enable getty@tty1.service getty@tty2.service getty@tty3.service >/dev/null 2>&1 || true

# ---------------------------------------------------------------- visible menu
GRUBD=/etc/default/grub
if [ -f "$GRUBD" ]; then
    [ -f "$GRUBD.bak-monocell" ] || cp -p "$GRUBD" "$GRUBD.bak-monocell"
    set_key() {
        key=$1; val=$2
        if grep -q "^$key=" "$GRUBD"; then
            sed -i "s|^$key=.*|$key=$val|" "$GRUBD"
        else
            echo "$key=$val" >> "$GRUBD"
        fi
    }
    set_key GRUB_TIMEOUT 5
    set_key GRUB_TIMEOUT_STYLE menu
    sed -i 's/^GRUB_HIDDEN_TIMEOUT=/#GRUB_HIDDEN_TIMEOUT=/' "$GRUBD" || true
    if [ -d /boot/grub2 ] && command -v grub2-mkconfig >/dev/null 2>&1; then
        grub2-mkconfig -o /boot/grub2/grub.cfg >/dev/null 2>&1 \
            && echo "GRUB menu: visible for 5 s, grub.cfg regenerated" \
            || echo "warning: grub2-mkconfig failed - the menu may need a manual run" >&2
    elif [ -d /boot/grub ] && command -v grub-mkconfig >/dev/null 2>&1; then
        grub-mkconfig -o /boot/grub/grub.cfg >/dev/null 2>&1 \
            && echo "GRUB menu: visible for 5 s, grub.cfg regenerated" \
            || echo "warning: grub-mkconfig failed" >&2
    else
        echo "warning: grub2-mkconfig not found - the menu timeout was not applied" >&2
    fi
fi

# ---------------------------------------------------------------- BLS / GRUB entry
# Important: write the entry AFTER grub2-mkconfig. On Nobara the mkconfig run
# normalises the BLS options back to the kernel defaults and drops systemd.unit.
KREL=$(uname -r)
SRC_ENTRY=$(ls "$BLS_DIR"/*-"$KREL".conf 2>/dev/null | head -n1 || true)
if [ -z "$SRC_ENTRY" ]; then
    echo "warning: no BLS entry for kernel $KREL in $BLS_DIR - GRUB entry skipped" >&2
    echo "         (is this a BLS/GRUB system?)" >&2
else
    python3 - "$SRC_ENTRY" "$BLS_ENTRY" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
out, seen_title, seen_opts = [], False, False
for line in open(src).read().splitlines():
    if line.startswith('title '):
        out.append('title MONOCELL - TUI')
        seen_title = True
    elif line.startswith('options '):
        opts = [o for o in line.split()[1:] if not o.startswith('systemd.unit=')]
        opts.append('systemd.unit=monocell.target')
        out.append('options ' + ' '.join(opts))
        seen_opts = True
    else:
        out.append(line)
if not seen_title:
    out.insert(0, 'title MONOCELL - TUI')
if not seen_opts:
    out.append('options systemd.unit=monocell.target')
open(dst, 'w').write('\n'.join(out) + '\n')
PY
    grep -q 'systemd.unit=monocell.target' "$BLS_ENTRY" || {
        echo "error: could not write the GRUB entry" >&2
        exit 1
    }
    echo "GRUB entry: $BLS_ENTRY (kernel $KREL)"
fi

# ---------------------------------------------------------------- groups
usermod -aG video,input "$USER_NAME" 2>/dev/null || true
echo "groups: $USER_NAME gains video,input (active at the next login)"

# ---------------------------------------------------------------- polkit / udev
if printf '%s' "$USER_NAME" | grep -q '^[A-Za-z0-9_-][A-Za-z0-9_.-]*$'; then
    mkdir -p /etc/polkit-1/rules.d
    cat > /etc/polkit-1/rules.d/49-monocell-power.rules <<RULE
// monocell: reboot/shutdown from the console (no polkit agent on the VT)
polkit.addRule(function(action, subject) {
    if (subject.user == "$USER_NAME" &&
        ["org.freedesktop.login1.reboot", "org.freedesktop.login1.poweroff",
         "org.freedesktop.login1.reboot-multiple-sessions",
         "org.freedesktop.login1.poweroff-multiple-sessions"].indexOf(action.id) >= 0) {
        return polkit.Result.YES;
    }
});
RULE
    cat > /etc/polkit-1/rules.d/50-monocell-media.rules <<RULE
// monocell: mount/eject removable media from the console (no polkit agent on the VT)
polkit.addRule(function(action, subject) {
    if (subject.user == "$USER_NAME" &&
        ["org.freedesktop.udisks2.filesystem-mount",
         "org.freedesktop.udisks2.eject-media"].indexOf(action.id) >= 0) {
        return polkit.Result.YES;
    }
});
RULE
    chmod 644 /etc/polkit-1/rules.d/49-monocell-power.rules \
              /etc/polkit-1/rules.d/50-monocell-media.rules
    echo "polkit: reboot/shutdown and removable media allowed for $USER_NAME"
fi
cat > /etc/udev/rules.d/90-monocell-backlight.rules <<'RULE'
# Let the video group set the backlight brightness (monocell brightness popup/hotkeys).
ACTION=="add", SUBSYSTEM=="backlight", RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness", RUN+="/bin/chmod 0660 /sys/class/backlight/%k/brightness"
RULE
udevadm control --reload 2>/dev/null || true
echo "udev: backlight rule installed"

# ---------------------------------------------------------------- autostart
PROFILE=$(profile_block)
if [ -n "$PROFILE" ]; then
    [ -f "$PROFILE" ] || : > "$PROFILE"
    [ -f "$PROFILE.bak-monocell" ] || cp -a "$PROFILE" "$PROFILE.bak-monocell"
    python3 - "$PROFILE" <<'PY'
import sys
p = sys.argv[1]
block = '''# >>> monocell console >>>
# added by deploy/nobara/install-frontend.sh - remove this block for a plain shell login
if [ -z "${SSH_CONNECTION:-}" ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ] \\
   && [ -z "${MONOCELL_NO_AUTOSTART:-}" ]; then
    if [ "$(tty 2>/dev/null)" = "/dev/tty1" ] && command -v monocell >/dev/null 2>&1; then
        TERM=linux exec monocell
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
    echo "autostart: $PROFILE (tty1 -> monocell)"
fi

echo
echo "done. Reboot and pick \"MONOCELL - TUI\" in the GRUB menu for a text login"
echo "on tty1 (monocell starts after login, tty2/3 give a plain shell)."
echo "The normal graphical boot stays the default. Re-run after a kernel update."
