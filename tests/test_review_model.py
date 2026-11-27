#!/usr/bin/env python3
"""roles.md FR-REV-13: `drover config model review [PICK] --builder M` compares model families,
so a full model id and its short name count as the same model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def model(builder, **review):
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.json"
        cfg.write_text(json.dumps({"project": {"owner": "o", "number": 1}, "roles": {"review": review}}))
        r = subprocess.run([sys.executable, str(ROOT / "lib/drover-config.py"), "model", "review", "--builder", builder],
                           capture_output=True, text=True, env=dict(os.environ, DROVER_CONFIG=str(cfg)), cwd=tmp)
        return r.returncode, r.stdout.strip()


class ReviewModel(unittest.TestCase):
    def test_same_family_by_full_id(self):
        self.assertEqual(model("claude-sonnet-5-5"), (0, "opus"))
        self.assertEqual(model("sonnet", model="claude-sonnet-5-5"), (0, "opus"))
        self.assertEqual(model("claude-opus-5-5", model="opus", alt_model="sonnet"), (0, "sonnet"))

    def test_other_family_keeps_the_review_model(self):
        self.assertEqual(model("claude-opus-5-5"), (0, "sonnet"))
        self.assertEqual(model("my-local-model"), (0, "sonnet"))

    def test_both_in_the_builder_family_halts(self):
        self.assertEqual(model("claude-sonnet-5-5", model="sonnet", alt_model="sonnet[1m]")[0], 65)


if __name__ == "__main__":
    unittest.main()
