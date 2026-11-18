#!/usr/bin/env bash
# drover installer: links bin/drover into ~/.local/bin and copies nothing into any project
# (specs/config.md FR-CFG-7). `drover skills install` links the skills into the harness's
# user skill folder; `drover onboard`, run inside a project, writes its .drover/config.json.
# Upgrading is `git pull` in this folder. Usage: ./install.sh
set -euo pipefail

case "${1:-}" in
  -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
  '') ;;
  *) echo "usage: ./install.sh  (it takes no project: run 'drover onboard' inside the project)" >&2; exit 64 ;;
esac

HERE=$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BIN_DIR="$HOME/.local/bin"
LINK="$BIN_DIR/drover"
mkdir -p "$BIN_DIR"
if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
  echo "$LINK exists and is not a link; left alone. Move it, then run ./install.sh again." >&2
  exit 1
fi
ln -sfn "$HERE/bin/drover" "$LINK"
echo "drover $(tr -d '[:space:]' < "$HERE/VERSION") linked: $LINK -> $HERE/bin/drover"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "add $BIN_DIR to PATH (e.g. in ~/.zshrc or ~/.bashrc)" ;;
esac
echo "next: drover skills install, then in a project: drover onboard (or /drover onboard)"
