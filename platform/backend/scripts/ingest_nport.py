"""Ingest a quarterly SEC N-PORT data-set ZIP into the nport_holdings table.

Usage (from platform/backend, main venv):
    python scripts/ingest_nport.py path\\to\\2026q1_nport.zip 2026q1 [--issuer AAL "AMERICAN AIRLINES"]

Download the quarter's ZIP first from https://www.sec.gov/about/dera_form-n-port-data-sets
(drop-file pattern — the ZIP is ~1GB and is streamed, never extracted). Without --issuer,
every ticker in the snapshots screening table is ingested, matching on the stored issuer
name. Rerunning a quarter replaces that quarter's rows.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models                              # noqa: E402
from app.core.db import init_db, session_scope      # noqa: E402
from app.nport import ingest_zip, match_holdings_to_instruments  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    zip_path, quarter = argv[0], argv[1]
    issuers: dict[str, tuple[str, ...]] = {}
    if "--issuer" in argv:
        i = argv.index("--issuer")
        issuers[argv[i + 1].upper()] = tuple(p.upper() for p in argv[i + 2:]) or (argv[i + 1].upper(),)

    init_db()
    with session_scope() as session:
        if not issuers:
            for snap in session.query(models.Snapshot).all():
                if snap.issuer:
                    # first two words of the issuer name make a robust match pattern
                    words = snap.issuer.upper().split()
                    issuers[snap.ticker] = (" ".join(words[:2]),)
        if not issuers:
            print("No tracked issuers (snapshots table empty) and no --issuer given.")
            return 1
        print(f"Ingesting {zip_path} ({quarter}) for {list(issuers)} …")
        counts = ingest_zip(zip_path, session, issuers, quarter)
        for ticker in counts:
            match_holdings_to_instruments(session, ticker)
        print("Rows ingested:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
