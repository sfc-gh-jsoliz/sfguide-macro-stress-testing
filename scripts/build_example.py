#!/usr/bin/env python3
"""Build the hosted no-account example in docs/try/ (GitHub Pages).

Regenerates the seeded data, copies the captured Live AI results into docs/try/mock/,
and writes docs/.nojekyll so Pages serves every file as-is.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
subprocess.run([sys.executable, str(ROOT / "data" / "generate.py")], check=True)
mock = ROOT / "docs" / "try" / "mock"
mock.mkdir(parents=True, exist_ok=True)
for name in ("notes.json", "agent.json"):
    shutil.copyfile(ROOT / "data" / "sample" / name, mock / name)
(ROOT / "docs" / ".nojekyll").write_text("")
print("docs/try ready; docs/.nojekyll written")
