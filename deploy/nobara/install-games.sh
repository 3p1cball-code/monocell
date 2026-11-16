#!/bin/sh
# Console games for Monocell's Games menu on Fedora/Nobara.
#
# Run as root on the target machine:
#
#   sudo ./install-games.sh [--binaries DIR] [--dry-run]
#
# Two halves:
#
#   1. What Fedora ships is installed with dnf: crawl, cataclysm-dda, nethack,
#      nudoku, moon-buggy, chocolate-doom, openarena and the Freedoom WAD.
#
#   2. The small console games Debian has and Fedora does not - vitetris,
#      freesweep, ttysolitaire, pacman4console, ninvaders, Brogue and the
#      text-mode DOOM - are copied from a directory of x86-64 binaries into
#      /usr/local/games.  Those binaries are tiny, self-contained and run on
#      Fedora unchanged; the easiest source is a Debian box that already has
#      them (the X61s):
#
#        mkdir games
#        scp x61s:/usr/games/{vitetris,freesweep,ttysolitaire,pacman4console,ninvaders} games/
#        scp x61s:/usr/local/games/{brogue,doom-ascii,doom-ascii.bin} games/
#        scp -r games target:/tmp/ && ssh target sudo /tmp/install-games.sh --binaries /tmp/games
#
#      Anything missing from that directory is skipped and reported; the dnf
#      half still installs everything else.
#
# Monocell finds a game by its command path, then on PATH, then in
# /usr/local/games, /usr/games, /usr/bin and /usr/local/bin - so the menu works
# on Debian and Fedora without a per-distribution table.
set -eu
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

BINARIES=games
DRY=0
while [ $# -gt 0 ]; do
    case $1 in
        --binaries) BINARIES=$2; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" != 0 ] && [ "$DRY" = 0 ]; then
    echo "install-games: run as root (sudo $0)" >&2
    exit 1
fi

PKGS="crawl cataclysm-dda nethack nudoku moon-buggy chocolate-doom openarena freedoom"

echo "== packages =="
if [ "$DRY" = 1 ]; then
    echo "dnf install -y $PKGS"
else
    dnf install -y $PKGS
fi

DOOM_DIR=/usr/share/games/doom
echo "== freedoom WAD =="
if [ "$DRY" = 1 ]; then
    echo "link $DOOM_DIR/freedoom*.wad -> /usr/share/freedoom/"
else
    mkdir -p "$DOOM_DIR"
    for n in freedoom1 freedoom2; do
        src=
        for d in /usr/share/doom /usr/share/freedoom /usr/share/games/doom; do
            [ -f "$d/$n.wad" ] && { src=$d/$n.wad; break; }
        done
        if [ -n "$src" ] && [ ! -e "$DOOM_DIR/$n.wad" ]; then
            ln -s "$src" "$DOOM_DIR/$n.wad"
            echo "  $DOOM_DIR/$n.wad -> $src"
        fi
    done
fi

echo "== copied games from $BINARIES =="
mkdir -p /usr/local/games
for g in vitetris freesweep ttysolitaire pacman4console ninvaders brogue; do
    if [ -f "$BINARIES/$g" ]; then
        if [ "$DRY" = 1 ]; then
            echo "  install $BINARIES/$g -> /usr/local/games/$g"
        else
            install -m 755 "$BINARIES/$g" "/usr/local/games/$g"
            echo "  /usr/local/games/$g"
        fi
    else
        echo "  $g: not in $BINARIES, skipped"
    fi
done

# Fedora's cataclysm-dda (fc43/fc44) segfaults on start; the Debian build runs
# fine against the same data.  The wrapper lives in /usr/local/bin, which comes
# before /usr/bin on PATH, so the menu finds the working one.
if [ -f "$BINARIES/cataclysm" ]; then
    if [ "$DRY" = 1 ]; then
        echo "  install cataclysm.bin + wrapper in /usr/local/bin"
    else
        install -m 755 "$BINARIES/cataclysm" /usr/local/games/cataclysm.bin
        cat > /usr/local/bin/cataclysm <<'WRAP'
#!/bin/sh
# Fedora's own cataclysm binary crashes; this is the Debian build, pointed at
# whichever data directory the distribution installed.
for d in /usr/share/cataclysm-dda /usr/share/games/cataclysm-dda; do
    [ -d "$d" ] && exec /usr/local/games/cataclysm.bin --datadir "$d" "$@"
done
exec /usr/local/games/cataclysm.bin "$@"
WRAP
        chmod 755 /usr/local/bin/cataclysm
        echo "  /usr/local/bin/cataclysm -> cataclysm.bin (Fedora's build crashes)"
    fi
else
    echo "  cataclysm: not in $BINARIES, Fedora's own build is used"
fi

if [ -f "$BINARIES/doom-ascii.bin" ]; then
    if [ "$DRY" = 1 ]; then
        echo "  install doom-ascii.bin + wrapper"
    else
        install -m 755 "$BINARIES/doom-ascii.bin" /usr/local/games/doom-ascii.bin
        # own wrapper: it looks for the WAD in both places distributions use
        cat > /usr/local/games/doom-ascii <<'WRAP'
#!/bin/sh
# DOOM in the terminal (doom-ascii against the Freedoom IWAD).
wad=/usr/share/games/doom/freedoom1.wad
[ -f "$wad" ] || wad=/usr/share/doom/freedoom1.wad
[ -f "$wad" ] || wad=/usr/share/freedoom/freedoom1.wad
[ -f "$wad" ] || wad=/usr/share/games/doom/freedoom2.wad
[ -f "$wad" ] || wad=/usr/share/doom/freedoom2.wad
[ -f "$wad" ] || wad=/usr/share/freedoom/freedoom2.wad
cols=$(tput cols 2>/dev/null || echo 80)
lines=$(tput lines 2>/dev/null || echo 24)
# the renderer prints two characters per pixel (character aspect ratio)
s=$(( (640 + cols - 1) / cols ))
t=$(( (200 + lines - 1) / lines ))
[ "$t" -gt "$s" ] && s=$t
[ "$s" -lt 1 ] && s=1
exec /usr/local/games/doom-ascii.bin -iwad "$wad" -nocolor -chars ascii -scaling "$s" "$@"
WRAP
        chmod 755 /usr/local/games/doom-ascii
        echo "  /usr/local/games/doom-ascii (+ .bin)"
    fi
else
    echo "  doom-ascii.bin: not in $BINARIES, skipped"
fi

[ "$DRY" = 1 ] && echo "== what Monocell will find (dry run: nothing installed yet) ==" ||
    echo "== what Monocell will find =="
for g in crawl cataclysm nethack brogue doom-ascii vitetris nudoku freesweep \
         ttysolitaire pacman4console ninvaders moon-buggy chocolate-doom; do
    p=$(command -v "$g" 2>/dev/null || true)
    if [ -z "$p" ]; then
        for d in /usr/local/games /usr/games /usr/bin /usr/local/bin; do
            [ -x "$d/$g" ] && { p=$d/$g; break; }
        done
    fi
    if [ -n "$p" ]; then
        echo "  $g -> $p"
    else
        echo "  $g -> MISSING"
    fi
done
