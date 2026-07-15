"""Pre-build the hero-name overview cache so the demo opens instantly.

Runs the full live pipeline for each hero ticker and writes the snapshot to app/cache/. A live
run makes several Claude calls + large EDGAR downloads, so this takes a few minutes per name.

Usage (from backend/):
    .venv/Scripts/python.exe -m scripts.build_hero_cache            # all HERO_TICKERS, 3y
    .venv/Scripts/python.exe -m scripts.build_hero_cache AAL TSE    # specific names
    .venv/Scripts/python.exe -m scripts.build_hero_cache --years 3
"""
from __future__ import annotations

import sys
import time

# EDGAR/MD&A text and our progress messages contain non-cp1252 characters (e.g. "→"); force a
# UTF-8 console so printing them on Windows doesn't crash the build.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.core.config import get_settings
from app.pipeline import run_overview
from app.core.progress import ProgressEvent, ProgressLog


def main(argv: list[str]) -> int:
    years = 3
    tickers: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--years":
            years = int(argv[i + 1])
            i += 2
            continue
        tickers.append(a.upper())
        i += 1

    from app.core.db import init_db
    init_db()   # the app's lifespan does this; standalone runs must too (new tables)

    settings = get_settings()
    if not tickers:
        tickers = sorted(settings.hero_ticker_set)

    if not settings.llm_enabled:
        print("WARNING: ANTHROPIC_API_KEY not set — cache will lack the LLM sections (bridge, "
              "covenants, OBS). Set it in .env for a full hero cache.\n")

    print(f"Building hero cache for {tickers} (years={years})…\n")
    for tk in tickers:
        t0 = time.time()
        log = ProgressLog(sink=lambda e: print(f"  [{tk}] {e.pct or 0:>3}% {e.message}"))
        try:
            ov = run_overview(tk, years, progress=log, live=True)  # live=True forces a fresh build
            secs = time.time() - t0
            bridge = ov.economic_debt_bridge
            lev = bridge.economic_leverage.display if (bridge and bridge.economic_leverage) else "n/a"
            print(f"  [OK] {tk}: {ov.header.issuer} - {len(ov.sources)} filings, "
                  f"econ leverage {lev}, {len(ov.covenants)} covenant pkg(s)  ({secs:.0f}s)\n")
        except Exception as exc:
            print(f"  [FAIL] {tk}: {exc}\n")
    print("Done. Cached snapshots live in app/cache/ and are served when 'Run live' is off.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
