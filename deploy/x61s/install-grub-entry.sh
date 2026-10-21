#!/bin/sh
# Add an alternate GRUB menu entry that boots straight into monocell on tty1.
# The normal entry and the default boot target stay untouched.
# Run as root on the target (after install-vt.sh).
#
#   ./install-grub-entry.sh [--remove]
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
if [ "${1:-}" = "--remove" ]; then
    rm -f /etc/grub.d/40_monocell /etc/systemd/system/monocell.target
    systemctl daemon-reload 2>/dev/null || true
    update-grub
    echo "removed the monocell GRUB entry and target"
    exit 0
fi
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -x /usr/local/bin/monocell ] || { echo "run deploy/x61s/install-vt.sh first" >&2; exit 1; }
[ -f /boot/grub/grub.cfg ] || { echo "/boot/grub/grub.cfg not found (GRUB expected)" >&2; exit 1; }

# systemd target: a normal text login on tty1; monocell starts from ~/.profile after login
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
systemctl disable monocell@1.service monocell@2.service >/dev/null 2>&1 || true

# clone the current kernel's entry
TMP=$(mktemp)
python3 "$HERE/grub_entry.py" --cfg /boot/grub/grub.cfg --kernel "$(uname -r)" > "$TMP"
if ! grep -q 'systemd.unit=monocell.target' "$TMP"; then
    echo "could not generate the GRUB entry" >&2
    rm -f "$TMP"
    exit 1
fi
{
    echo '#!/bin/sh'
    echo "cat <<'TWENTRY'"
    cat "$TMP"
    echo 'TWENTRY'
} > /etc/grub.d/40_monocell
rm -f "$TMP"
chmod +x /etc/grub.d/40_monocell

# make the menu visible (backup once)
[ -f /etc/default/grub.bak-monocell ] || cp /etc/default/grub /etc/default/grub.bak-monocell
if grep -q '^GRUB_TIMEOUT=' /etc/default/grub; then
    sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub
else
    echo 'GRUB_TIMEOUT=5' >> /etc/default/grub
fi
if grep -q '^GRUB_TIMEOUT_STYLE=' /etc/default/grub; then
    sed -i 's/^GRUB_TIMEOUT_STYLE=.*/GRUB_TIMEOUT_STYLE=menu/' /etc/default/grub
else
    echo 'GRUB_TIMEOUT_STYLE=menu' >> /etc/default/grub
fi
sed -i 's/^GRUB_HIDDEN_TIMEOUT=/#GRUB_HIDDEN_TIMEOUT=/' /etc/default/grub || true

update-grub
grep -q "monocell (text console)" /boot/grub/grub.cfg || { echo "entry missing after update-grub" >&2; exit 1; }
echo
echo "GRUB entry installed: \"monocell (text console)\""
echo "At boot, select it for a text login on tty1 (fingerprint or password);"
echo "monocell starts from ~/.profile after login (see install-login.sh)."
echo "Default boot is unchanged. Re-run after a kernel update."
