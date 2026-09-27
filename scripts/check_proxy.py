#!/usr/bin/env python3
"""
Every path the frontend fetches must have a Vite dev-proxy entry.

A missing entry does not 404. The dev server answers with index.html and a 200,
so the failure surfaces as "backend unreachable" while the backend is fine. This
has bitten /rig and /kinematics; the check makes it loud instead.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
CONFIG = ROOT / "frontend" / "vite.config.ts"

# Any quoted string literal that looks like an API path, however it is reached:
# fetch(), the getJson() helper, an <img src>, or a template literal.
FETCH = re.compile(r"""[`'"](/[a-z][a-z0-9_]*)(?:/|[`'"?])""")
PROXY = re.compile(r"""'(/[a-z_]+)'\s*:""")


def main() -> int:
    used: set[str] = set()
    for path in list(SRC.rglob("*.ts")) + list(SRC.rglob("*.svelte")):
        used |= set(FETCH.findall(path.read_text()))

    proxied = set(PROXY.findall(CONFIG.read_text()))
    missing = sorted(used - proxied)

    for prefix in sorted(used):
        print(f"  {prefix:14} {'ok' if prefix in proxied else 'MISSING from vite.config.ts'}")

    if missing:
        print(f"\nfail: add {', '.join(missing)} to the proxy table in {CONFIG.relative_to(ROOT)}")
        return 1
    print("\nall fetched paths are proxied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
