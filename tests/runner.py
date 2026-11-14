"""Evaluation runner: scenarios x 5 phrasings x k runs x faults, end to end through the public API.

For every run the buyer agent goes from a sentence to a final state, and the runner checks
  1. understanding: did the parser get category, requirements, quantity and date right?
  2. outcome: did the purchase end in an allowed state?

Reported metrics:
  pass@1             share of single runs that passed
  pass^k             share of cases (scenario x phrasing x fault) where all k runs passed;
                     this is the reliability number, one flaky run fails the case
  false-block rate   runs that should have gone through but were stopped without a reason
                     (only counted for scenarios whose expected outcome includes PLACED)
  silent wrong       orders PLACED although the request was misunderstood, or although the
                     scenario or the active fault means a person should have been asked

Usage (repo root, venv active):
  python tests/runner.py --in-process                 # no server needed; uses OPENROUTER_API_KEY if set
  python tests/runner.py --k 3                        # against a running server (THE_PASS_URL or localhost:8000)
  python tests/runner.py --faults none,PARTIAL_STOCK --scenarios S1-milk-auto
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "backend")]

import buyer_agent  # noqa: E402
from pass_client import ApiError, PassClient  # noqa: E402

SCENARIOS = pathlib.Path(__file__).with_name("scenarios.yaml")
REPORTS = pathlib.Path(__file__).with_name("reports")
FAULTS = ["PRICE_CHANGE", "PARTIAL_STOCK", "TIMEOUT", "RATE_LIMIT", "EMPTY_RESULT"]

# Under a fault: extra end states that count as degrading safely, and whether an automatic
# PLACED is a silent wrong order (the fault means a person should have looked first).
FAULT_RULES: dict[Optional[str], tuple[set[str], bool]] = {
    None: (set(), False),
    "PRICE_CHANGE": ({"REQUOTE_REQUIRED", "PENDING_BUYER_APPROVAL", "BUYER_AUTHORIZATION_REQUIRED"}, False),
    "PARTIAL_STOCK": ({"PENDING_BUYER_APPROVAL", "BUYER_AUTHORIZATION_REQUIRED"}, True),
    "TIMEOUT": ({"ERROR UPSTREAM_ERROR", "PENDING_BUYER_APPROVAL", "BUYER_AUTHORIZATION_REQUIRED"}, True),
    "RATE_LIMIT": ({"ERROR RATE_LIMITED"}, False),
    "EMPTY_RESULT": ({"NO_CANDIDATES"}, True),
}
BLOCKING = {"NEEDS_INPUT", "NO_CANDIDATES", "REJECTED", "NO_ORDER"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class RunResult:
    scenario: str
    phrasing_index: int
    phrasing: str
    fault: Optional[str]
    run: int
    status: str
    mismatches: list[str] = field(default_factory=list)
    passed: bool = False
    silent_wrong: bool = False
    false_block: bool = False
    expects_placed: bool = False      # the scenario's allowed outcomes include PLACED
    seconds: float = 0.0


def expected_date(spec: str, today: date) -> date:
    if spec == "tomorrow":
        return today + timedelta(days=1)
    if spec.startswith("next_"):
        wd = WEEKDAYS.index(spec[5:])
        return today + timedelta(days=(wd - today.weekday() - 1) % 7 + 1)
    if spec.startswith("in_") and spec.endswith("_days"):
        return today + timedelta(days=int(spec[3:-5]))
    raise ValueError(f"Unknown delivery spec {spec!r}")


def check_understanding(expect: dict, structured: Optional[dict], today: date) -> list[str]:
    if structured is None:
        return ["request was not understood at all"]
    out = []
    if "category" in expect and structured["category"] != expect["category"]:
        out.append(f"category {structured['category']} != {expect['category']}")
    if "requirements" in expect:
        got = sorted(r["code"] for r in structured["requirements"])
        if got != sorted(expect["requirements"]):
            out.append(f"requirements {got} != {sorted(expect['requirements'])}")
    if "quantity" in expect:
        q, e = structured["quantity"], expect["quantity"]
        if (round(q["value"], 3), q["unit"]) != (round(float(e["value"]), 3), e["unit"]):
            out.append(f"quantity {q['value']:g} {q['unit']} != {e['value']} {e['unit']}")
    if "delivery" in expect:
        want = expected_date(expect["delivery"], today).isoformat()
        if structured["delivery_date"] != want:
            out.append(f"delivery {structured['delivery_date']} != {want}")
    return out


def judge(r: RunResult, expect: dict, understood: list[str]) -> RunResult:
    ok_under_fault, placed_is_wrong = FAULT_RULES[r.fault]
    final = set(expect["final"])
    r.mismatches = understood
    r.expects_placed = "PLACED" in final
    r.passed = not understood and (r.status in final or r.status in ok_under_fault)
    r.silent_wrong = r.status == "PLACED" and (bool(understood) or "PLACED" not in final or placed_is_wrong)
    stopped = r.status in BLOCKING or r.status.startswith("ERROR")
    r.false_block = "PLACED" in final and stopped and r.status not in ok_under_fault
    return r


def set_fault(client: PassClient, name: str, enabled: bool) -> None:
    try:
        client.call("PUT", f"/v1/admin/faults/{name}", {"enabled": enabled})
    except ApiError as e:
        print(f"  ! could not {'enable' if enabled else 'disable'} fault {name}: {e}", file=sys.stderr)


def run_all(client: PassClient, scenarios: list[dict], faults: list[Optional[str]], k: int,
            poll_every_s: float = 1.0, today: Optional[date] = None, quiet: bool = False) -> list[RunResult]:
    today = today or date.today()
    results: list[RunResult] = []
    for fault in faults:
        if fault:
            set_fault(client, fault, True)
        try:
            for s in scenarios:
                for i, text in enumerate(s["phrasings"]):
                    for n in range(k):
                        t0 = time.perf_counter()
                        out = buyer_agent.run(client, text, s["protocol"], s.get("profile"),
                                              poll_every_s=poll_every_s, max_polls=3)
                        r = RunResult(s["id"], i, text, fault, n, out.status, seconds=time.perf_counter() - t0)
                        results.append(judge(r, s["expect"], check_understanding(s["expect"], out.structured, today)))
                        if not quiet:
                            mark = "ok " if r.passed else "SILENT WRONG" if r.silent_wrong else "FAIL"
                            why = f"  ({'; '.join(r.mismatches)})" if r.mismatches else ""
                            print(f"  {mark:<4} {s['id']:<26} p{i} {fault or '-':<13} {r.status:<28}{why}")
        finally:
            if fault:
                set_fault(client, fault, False)
    return results


def summarize(results: list[RunResult]) -> dict:
    cases: dict[tuple, list[RunResult]] = defaultdict(list)
    for r in results:
        cases[(r.scenario, r.phrasing_index, r.fault)].append(r)
    per_scenario: dict[str, list[bool]] = defaultdict(list)
    for (sid, _, _), rs in cases.items():
        per_scenario[sid].append(all(r.passed for r in rs))
    eligible = [r for r in results if r.expects_placed]
    return {
        "runs": len(results),
        "cases": len(cases),
        "pass@1": round(sum(r.passed for r in results) / len(results), 3) if results else None,
        "pass^k": round(sum(all(r.passed for r in rs) for rs in cases.values()) / len(cases), 3) if cases else None,
        "false_block_rate": round(sum(r.false_block for r in eligible) / len(eligible), 3) if eligible else 0.0,
        "silent_wrong_orders": sum(r.silent_wrong for r in results),
        "per_scenario_pass^k": {sid: round(sum(v) / len(v), 3) for sid, v in per_scenario.items()},
        "mean_seconds": round(sum(r.seconds for r in results) / len(results), 2) if results else None,
    }


def print_summary(s: dict, k: int, model: str) -> None:
    print(f"\nModel: {model}   k={k}   runs={s['runs']}   cases={s['cases']}")
    print(f"pass@1 {s['pass@1']}   pass^{k} {s['pass^k']}   false-block rate {s['false_block_rate']}   "
          f"silent wrong orders {s['silent_wrong_orders']}   mean {s['mean_seconds']}s/run")
    for sid, v in s["per_scenario_pass^k"].items():
        print(f"  {sid:<28} pass^{k} {v}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--faults", default="none", help=f"comma list of none,{','.join(FAULTS)} or 'all'")
    ap.add_argument("--scenarios", help="comma list of scenario ids (default: all)")
    ap.add_argument("--in-process", action="store_true", help="run against the app in this process, no server needed")
    ap.add_argument("--url")
    ap.add_argument("--out", type=pathlib.Path, help="JSON report path (default: tests/reports/runner-<time>.json)")
    args = ap.parse_args()

    scenarios = yaml.safe_load(SCENARIOS.read_text())["scenarios"]
    if args.scenarios:
        wanted = set(args.scenarios.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
    names = ["none", *FAULTS] if args.faults == "all" else args.faults.split(",")
    faults = [None if f == "none" else f for f in names]

    if args.in_process:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.orchestrator import parser_api
        client, model, poll = PassClient(http=TestClient(app)), parser_api.llm.model if parser_api.llm.enabled else "rules", 0.0
    else:
        client, model, poll = PassClient(base_url=args.url), "(server-side)", 1.0

    results = run_all(client, scenarios, faults, args.k, poll_every_s=poll)
    summary = summarize(results)
    print_summary(summary, args.k, model)

    out = args.out or REPORTS / f"runner-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": model, "k": args.k, "summary": summary,
                               "runs": [asdict(r) for r in results]}, indent=2, ensure_ascii=False))
    print(f"\nReport: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")


if __name__ == "__main__":
    main()
