"""No emoji in text drover writes (specs/conventions.md FR-CNV-3, specs/README.md SC-7).

Scans every shipped file under lib/, bin/, harnesses/, skills/, hooks/, workflows/, .github/, .claude-plugin/,
install.sh, README.md and CLAUDE.md: the comments, block notes, PR body text, logs, status
output and chat output all come from there.
Exempt (FR-CNV-3): the visual skill's page assets, which are page design. Arrows (→ ↳ ←) and
box-drawing characters are not emoji and stay.

Run directly:  python3 tests/test_no_emoji.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPE = ("lib/", "bin/", "harnesses/", "skills/", "hooks/", "workflows/", "install.sh", "README.md", "CLAUDE.md", ".github/", ".claude-plugin/")
EXEMPT = ("skills/visual/assets/",)

# Unicode Emoji=Yes code points, minus the ASCII keycap bases, (c) (R) (TM) and the arrows.
EMOJI = re.compile(
    "["
    "‼⁉ℹ⌚⌛⌨⏏⏩-⏳⏸-⏺Ⓜ▪▫▶◀◻-◾"
    "☀-☄☎☑☔☕☘☝☠☢☣☦☪☮☯☸-☺"
    "♀♂♈-♓♟♠♣♥♦♨♻♾♿⚒-⚗⚙⚛⚜"
    "⚠⚡⚧⚪⚫⚰⚱⚽⚾⛄⛅⛈⛎⛏⛑⛓⛔⛩⛪"
    "⛰-⛵⛷-⛺⛽✂✅✈-✍✏✒✔✖✝✡✨✳✴"
    "❄❇❌❎❓-❕❗❣❤➕-➗➰➿⬛⬜⭐⭕"
    "〰〽㊗㊙️"
    "\U0001F000-\U0001FAFF"
    "]"
)


def shipped() -> list[str]:
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files", *SCOPE], capture_output=True, text=True, check=True)
    return [f for f in files.stdout.splitlines() if not f.startswith(EXEMPT)]


def test_detector_catches_emoji_and_spares_arrows():
    for s in ("✅ done", "🙋 needs you", "⚠️ warning", "🛡 guard", "⏳ wait", "▶︎ run", "💀 zombie"):
        assert EMOJI.search(s), s
    for s in ("a → b", "↳ proof", "← back", "↻ 2/3", "┌─ Ready ─┐", "× 2", "≥ 70", "…", "·", "✓ ok", "▎Workers"):
        assert not EMOJI.search(s), s


def test_no_emoji_in_shipped_text():
    hits = []
    for rel in shipped():
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if EMOJI.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not hits, f"{len(hits)} line(s) with emoji:\n" + "\n".join(hits[:40])


def test_scope_is_not_empty():
    files = shipped()
    assert any(f.startswith("lib/") for f in files) and "bin/drover" in files and "install.sh" in files
    assert not any(f.startswith(EXEMPT) for f in files)


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed.")
    sys.exit(1 if failed else 0)
