#!/bin/sh
# Start monocell from the source tree.
cd "$(dirname "$0")" && PYTHONPATH="$(pwd):${PYTHONPATH}" exec python3 -m monocell "$@"
