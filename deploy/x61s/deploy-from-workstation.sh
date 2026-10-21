#!/bin/sh
# Push this tree to the X61s and install there.
#   ./deploy-from-workstation.sh alex@x61s [--no-desktop] [...installer options]
set -eu
TARGET=${1:?usage: $0 user@host [install-vt options]}
shift || true
SRC=$(cd "$(dirname "$0")/../.." && pwd)
echo "copying to $TARGET:/tmp/monocell-src ..."
rsync -a --delete \
    --exclude '.git' --exclude '.shots' --exclude '.*home' --exclude 'tmpaudio' --exclude '__pycache__' \
    "$SRC/" "$TARGET:/tmp/monocell-src/"
echo "installing on $TARGET ..."
# shellcheck disable=SC2029
ssh -t "$TARGET" "sudo /tmp/monocell-src/deploy/x61s/install-vt.sh --user ${TARGET%@*} $*"
