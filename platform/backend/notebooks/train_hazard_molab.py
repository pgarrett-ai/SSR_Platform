"""Train the SSR Platform hazard model in the cloud (molab.marimo.io).

Create a notebook at https://molab.marimo.io -> "New notebook" -> paste this file's
GitHub URL. Everything it needs installs in the first cell; the committed label/universe
caches ship with the clone, so the run only fetches XBRL fundamentals + prices.

GPU: NOT needed — the model is a seconds-long sklearn fit; runtime is network fetches.
The GPU toggle in the molab header (RTX Pro 6000) does nothing for this workload.
"""
import marimo

app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell
def _(mo):
    mo.md(
        """
        # SSR Platform — hazard-model training

        Trains the distress-hazard model on **real data**: 8-K Item 1.03 bankruptcies +
        Fitch 17g-7 D/RD events as labels, controls from the point-in-time 10-K filer
        universe, EDGAR XBRL fundamentals and point-in-time market features per firm-year.
        Walk-forward validated; PDs prior-corrected to the measured base rate.
        """
    )
    return


@app.cell
def _():
    # clone the repo and install its backend requirements (idempotent, ~1-2 min cold)
    import pathlib
    import subprocess
    import sys

    repo = pathlib.Path("SSR_Platform")
    if not repo.exists():
        subprocess.run(["git", "clone", "--depth", "1",
                        "https://github.com/pgarrett-ai/SSR_Platform.git"], check=True)
    backend = repo / "platform" / "backend"
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                    str(backend / "requirements.txt")], check=True)
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    return (backend,)


@app.cell
def _(mo):
    ua = mo.ui.text(label="SEC User-Agent — your name + email (EDGAR requires it)",
                    placeholder="Jane Doe jane@example.com", full_width=True)
    defaulters = mo.ui.slider(20, 1000, value=60, label="defaulter firms", show_value=True)
    controls = mo.ui.slider(20, 4000, value=240, label="control firms", show_value=True)
    mo.vstack([ua, defaulters, controls,
               mo.md("_Defaults (60/240) finish in a few minutes; the full panel "
                     "(all defaulters, 1:4 controls) is a multi-hour EDGAR fetch._")])
    return controls, defaulters, ua


@app.cell
def _(backend, controls, defaulters, mo, ua):
    import datetime as dt
    import os

    mo.stop(not ua.value.strip(), mo.md("**Enter a SEC User-Agent above to start.**"))
    os.environ["SEC_USER_AGENT"] = ua.value.strip()
    assert (backend / "app").exists()

    from app.hazard.labels import (annual_default_rate, build_real_panel,
                                   load_or_harvest_events, load_or_harvest_universe)
    from app.hazard.train import train_from_panel

    events = load_or_harvest_events(2010)
    # Shuffle so a partial run samples firms uniformly instead of chronologically.
    # Leak-free: every row is point-in-time per firm-year and walk-forward folds
    # split on the observation's calendar year — order changes WHICH firms enter,
    # never a row's temporal content.
    import random
    random.Random(0).shuffle(events)
    print(f"{len(events)} default events across {len({e['cik'] for e in events})} firms")
    df = build_real_panel(events, defaulters.value, controls.value, start_year=2010)
    true_rate = annual_default_rate(events, load_or_harvest_universe(2010))
    aucs, bundle = train_from_panel(df, save=True, meta={
        "label_source": (f"molab run {dt.date.today()}: 8-K Item 1.03 + Fitch 17g-7 D/RD, "
                         f"{int(df['label'].sum())} default firm-years / {len(df)} rows"),
        "prior": {"sample_rate": float(df["label"].mean()), "true_rate": true_rate}})
    print("walk-forward AUC:", {y: round(a, 3) for y, a in sorted(aucs.items())})
    evaluation = bundle.get("eval") or {}
    print("operating points:", evaluation.get("operating_points"))
    return (bundle,)


@app.cell
def _(bundle, mo):
    from app.hazard.train import MODEL_PATH

    mo.download(data=MODEL_PATH.read_bytes(), filename="trained_hazard.joblib",
                label=f"download trained bundle ({bundle['n_rows']} firm-years)")
    return


if __name__ == "__main__":
    app.run()
