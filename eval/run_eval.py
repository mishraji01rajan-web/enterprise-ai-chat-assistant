"""Evaluation harness: runs eval/cases.py against the real agent graph and
produces eval/results.json + eval/results.md.

Usage:
    python -m eval.run_eval

Resets the SQL DB and vector store to the canonical seed dataset first (so
results are reproducible), then runs every case through `run_agent`, checking
routing correctness, retrieval/citation correctness, SQL/tool scoping, and
no-hallucination behavior, and reports latency + pass/fail per case plus
aggregate metrics (retrieval hit rate, citation accuracy, latency, failure
rate, agent/tool execution correctness).

Runs against whatever LLM_PROVIDER is configured in the environment
(defaults to "offline" — fully deterministic, no API key needed). Point it at
a real provider to also evaluate live answer quality.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.cases import CASES, EvalCase

from app.agent.graph import run_agent
from app.auth.schemas import CurrentUser
from app.config import settings
from app.db.models import Conversation, Role
from app.db.seed import seed
from app.db.session import get_session
from app.rag.ingest import ingest_all

CURRENT_USERS = {
    "admin": CurrentUser(id=1, username="admin", full_name="Priya Nair", role=Role.ADMIN, customer_id=None),
    "employee": CurrentUser(id=3, username="finance.morgan", full_name="Morgan Ellis", role=Role.EMPLOYEE, customer_id=None),
    "acme_customer": CurrentUser(id=4, username="acme.customer", full_name="Sam Rivera", role=Role.CUSTOMER, customer_id=1),
    "blueharbor_customer": CurrentUser(id=5, username="blueharbor.customer", full_name="Riley Chen", role=Role.CUSTOMER, customer_id=2),
}

RESULTS_JSON = Path(__file__).resolve().parent / "results.json"
RESULTS_MD = Path(__file__).resolve().parent / "results.md"


def _check_case(case: EvalCase, result, error: str | None) -> dict:
    checks: list[tuple[str, bool]] = []

    if error is not None:
        return {"case_id": case.id, "passed": False, "checks": [("no_exception", False)], "error": error}

    if case.expect_needs_rag is not None:
        checks.append(("needs_rag", result.needs_rag == case.expect_needs_rag))
    if case.expect_needs_sql is not None:
        checks.append(("needs_sql", result.needs_sql == case.expect_needs_sql))
    if case.expect_needs_tool is not None:
        checks.append(("needs_tool", result.needs_tool == case.expect_needs_tool))

    if case.expect_citations_include:
        cited_ids = {c["doc_id"] for c in result.citations}
        hit = any(doc_id in cited_ids for doc_id in case.expect_citations_include)
        checks.append(("citations_include_expected", hit))

    if case.expect_no_citations:
        checks.append(("no_citations", len(result.citations) == 0))

    if case.expect_answer_contains_any:
        answer_lower = result.final_answer.lower()
        checks.append(("answer_signals_uncertainty", any(kw in answer_lower for kw in case.expect_answer_contains_any)))

    if case.expect_pending_approval is not None:
        has_pending = result.pending_approval_id is not None
        checks.append(("pending_approval_matches", has_pending == case.expect_pending_approval))

    if case.expect_sql_scoped_customer_id is not None:
        scoped = result.sql_query_text is not None and f"customer_id = {case.expect_sql_scoped_customer_id}" in result.sql_query_text
        checks.append(("sql_scoped_to_expected_customer", scoped))

    if case.expect_tool_scoped_customer_id is not None:
        tr = result.tool_result or {}
        scoped = tr.get("customer_id") == case.expect_tool_scoped_customer_id
        checks.append(("tool_scoped_to_expected_customer", scoped))

    if case.expect_sql_tables_only is not None and result.sql_query_text:
        from_clause = result.sql_query_text.lower()
        disallowed = [t for t in ("users", "conversations", "messages", "pending_approvals") if t in from_clause]
        checks.append(("sql_touches_only_allowed_tables", len(disallowed) == 0))

    checks.append(("not_aborted", not result.aborted))

    passed = all(ok for _, ok in checks)
    return {"case_id": case.id, "passed": passed, "checks": checks, "error": None}


def run_all() -> dict:
    print("[eval] Resetting SQL DB and re-ingesting knowledge base for a reproducible run...")
    seed()
    ingest_all()

    case_results = []
    latencies = []
    retrieval_hits = 0
    retrieval_cases = 0
    failures = 0

    for case in CASES:
        user = CURRENT_USERS[case.user_key]
        conv_id = str(uuid.uuid4())
        with get_session() as db:
            now = datetime.utcnow()
            db.add(Conversation(id=conv_id, user_id=user.id, title="eval", created_at=now, updated_at=now))
            db.flush()

            start = time.perf_counter()
            error = None
            result = None
            try:
                result = run_agent(db, user, conv_id, case.history, case.query)
            except Exception as exc:  # noqa: BLE001 - eval must capture, not crash, on any failure
                error = f"{type(exc).__name__}: {exc}"
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

        outcome = _check_case(case, result, error)
        outcome["category"] = case.category
        outcome["description"] = case.description
        outcome["latency_ms"] = result.duration_ms if result else elapsed_ms
        outcome["needs_rag"] = result.needs_rag if result else None
        outcome["needs_sql"] = result.needs_sql if result else None
        outcome["needs_tool"] = result.needs_tool if result else None
        outcome["citations"] = result.citations if result else []
        outcome["answer_preview"] = (result.final_answer[:200] if result else "")
        case_results.append(outcome)

        latencies.append(outcome["latency_ms"])
        if error or (result and result.aborted):
            failures += 1
        if case.expect_citations_include:
            retrieval_cases += 1
            cited_ids = {c["doc_id"] for c in (result.citations if result else [])}
            if any(d in cited_ids for d in case.expect_citations_include):
                retrieval_hits += 1

        status = "PASS" if outcome["passed"] else "FAIL"
        print(f"  [{status}] {case.id} ({case.category}) - {case.description} - {outcome['latency_ms']}ms")

    total = len(case_results)
    passed = sum(1 for r in case_results if r["passed"])

    summary = {
        "generated_at": datetime.utcnow().isoformat(),
        "llm_provider": settings.llm_provider,
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "failure_rate": round(failures / total, 3) if total else 0.0,
        "retrieval_hit_rate": round(retrieval_hits / retrieval_cases, 3) if retrieval_cases else None,
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
            "median": round(statistics.median(latencies), 1) if latencies else None,
            "p95": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1) if latencies else None,
            "max": round(max(latencies), 1) if latencies else None,
        },
        "by_category": {},
    }

    for cat in sorted({c.category for c in CASES}):
        cat_results = [r for r in case_results if r["category"] == cat]
        cat_passed = sum(1 for r in cat_results if r["passed"])
        summary["by_category"][cat] = {
            "total": len(cat_results),
            "passed": cat_passed,
            "pass_rate": round(cat_passed / len(cat_results), 3) if cat_results else 0.0,
        }

    return {"summary": summary, "cases": case_results}


def render_markdown(report: dict) -> str:
    s = report["summary"]
    retrieval_hit_rate_str = "N/A" if s["retrieval_hit_rate"] is None else f"{s['retrieval_hit_rate'] * 100:.1f}%"
    lines = [
        "# Evaluation Results",
        "",
        f"Generated: {s['generated_at']}  ",
        f"LLM provider: `{s['llm_provider']}`",
        "",
        "## Summary",
        "",
        f"- **Total cases:** {s['total_cases']}",
        f"- **Passed:** {s['passed']} ({s['pass_rate'] * 100:.1f}%)",
        f"- **Failed:** {s['failed']}",
        f"- **Failure rate (aborted/exception):** {s['failure_rate'] * 100:.1f}%",
        f"- **Retrieval hit rate** (expected doc cited, RAG-tagged cases): {retrieval_hit_rate_str}",
        f"- **Latency (ms):** mean={s['latency_ms']['mean']}, median={s['latency_ms']['median']}, "
        f"p95={s['latency_ms']['p95']}, max={s['latency_ms']['max']}",
        "",
        "### By category",
        "",
        "| Category | Passed | Total | Pass rate |",
        "|---|---|---|---|",
    ]
    for cat, stats in s["by_category"].items():
        lines.append(f"| {cat} | {stats['passed']} | {stats['total']} | {stats['pass_rate'] * 100:.1f}% |")

    lines += ["", "## Case detail", "", "| ID | Category | Description | Result | Latency (ms) | Notes |", "|---|---|---|---|---|---|"]
    for r in report["cases"]:
        status = "✅ PASS" if r["passed"] else "❌ FAIL"
        failed_checks = ", ".join(name for name, ok in r.get("checks", []) if not ok) or (r["error"] or "")
        lines.append(f"| {r['case_id']} | {r['category']} | {r['description']} | {status} | {r['latency_ms']} | {failed_checks} |")

    lines += ["", "## Notes", "", (
        "Cases assert routing correctness (needs_rag/needs_sql/needs_tool), retrieval/citation "
        "correctness, SQL/tool row-level scoping, human-approval gating, and no-hallucination "
        "behavior — all of which are deterministic regardless of LLM provider. Free-text answer "
        "*prose quality* is inherently LLM-dependent; run with `LLM_PROVIDER=anthropic` (or "
        "openai/gemini) and a valid API key to evaluate live answer quality on top of these "
        "structural checks."
    )]
    return "\n".join(lines)


if __name__ == "__main__":
    report = run_all()
    RESULTS_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    RESULTS_MD.write_text(render_markdown(report), encoding="utf-8")
    print(f"\nWrote {RESULTS_JSON} and {RESULTS_MD}")
    print(f"Summary: {report['summary']['passed']}/{report['summary']['total_cases']} passed")
