#!/usr/bin/env python3
"""Run the Live AI paths once and save the results for Sample mode and the hosted example.

1. Checks that the Python scenario math equals MACRO_STRESS_SCENARIO for every preset.
2. Writes a stress note (AI_COMPLETE draft + AI_COMPLETE verifier) for every tier x preset,
   plus one note per preset with an injected unsupported forecast.
3. Asks the Cortex Agent the suggested questions.
Output: data/sample/notes.json, data/sample/agent.json, data/sample/parity.json
Needs the Live-mode environment variables (see README). Usage: python3 scripts/capture_sample.py
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import stress_core as core  # noqa: E402

QUESTIONS = [
    "Which credit tier had the highest average 30-day delinquency in 2026?",
    "How did holiday-season (Nov-Dec) transaction volume track against consumer sentiment over the last two years?",
    "How did BNPL transaction volume change between December 2025 and June 2026 by credit tier?",
    "What was the average charge-off rate by credit tier in Q1 2026 compared with Q3 2025?",
    "Which products had zero charge-offs, and what share of June 2026 volume did they carry?",
]


def main():
    out = ROOT / "data" / "sample"
    out.mkdir(exist_ok=True)
    sample = core.SampleSource()
    sessions = [core.live_session() for _ in range(4)]
    live = core.LiveSource(sessions[0])
    bench = live.benchmark()

    # 1. parity
    parity = []
    for name, f, u in core.PRESETS + [("Baseline", 0, 0)]:
        a, b = sample.scenario(f, u), live.scenario(f, u)
        cols = ["volume_stress", "approval_stress", "delinquency_stress", "charge_off_stress"]
        diff = max(float((a[c].values - b[c].values).__abs__().max()) for c in cols)
        same_status = list(a["status"]) == list(b["status"])
        parity.append(dict(preset=name, maxAbsDiff=diff, statusMatch=same_status))
        print("parity", name, diff, same_status)
        assert same_status and diff < 1e-6 * 1e3, "Sample math differs from MACRO_STRESS_SCENARIO"
    (out / "parity.json").write_text(json.dumps(parity, indent=1) + "\n")

    # 2. notes
    jobs = [(t, n, f, u, False) for n, f, u in core.PRESETS for t in ["All tiers"] + core.TIERS]
    jobs += [("All tiers", n, f, u, True) for n, f, u in core.PRESETS]

    def run(i_job):
        i, (t, n, f, u, inj) = i_job
        src = core.LiveSource(sessions[i % len(sessions)])
        for attempt in range(3):
            try:
                r = core.run_note(src, bench, t, f, u, inj)
                r["preset"] = n
                print(f"note {t} / {n} / injected={inj}: {r['verdict']['status']} in {r['latencyMs']} ms", flush=True)
                return r
            except Exception as e:
                print("retry", t, n, e, flush=True)
                time.sleep(3)
        raise SystemExit("note failed")

    with ThreadPoolExecutor(4) as ex:
        notes = list(ex.map(run, enumerate(jobs)))
    (out / "notes.json").write_text(json.dumps(notes, indent=1, default=str) + "\n")

    # 3. agent
    def ask(i_q):
        i, q = i_q
        src = core.LiveSource(sessions[i % len(sessions)])
        t0 = time.time()
        a = src.ask_agent(q)
        a.update(question=q, latencyMs=int((time.time() - t0) * 1000))
        print(f"agent {a['latencyMs']} ms: {q}", flush=True)
        return a

    with ThreadPoolExecutor(4) as ex:
        answers = list(ex.map(ask, enumerate(QUESTIONS)))
    (out / "agent.json").write_text(json.dumps(answers, indent=1, default=str) + "\n")


if __name__ == "__main__":
    main()
