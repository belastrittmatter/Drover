#!/usr/bin/env bash
# Compatibility shim: the Codex lane is drover-lane.sh --backend codex (same flags).
exec bash "$(dirname "${BASH_SOURCE[0]}")/drover-lane.sh" --backend codex "$@"
