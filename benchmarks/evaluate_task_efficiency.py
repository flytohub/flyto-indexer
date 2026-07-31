#!/usr/bin/env python3
"""Run the fixed 100-scenario task continuity and efficiency contract."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cli import build_parser  # noqa: E402
from src.task_cli import (  # noqa: E402
    execute_task_status,
    execute_usage_record,
    execute_usage_report,
)
from src.task_reports import render_task_status, render_usage_report  # noqa: E402
from src.task_runs import TaskRunStore, default_task_db, read_task_continuity  # noqa: E402
from src.task_usage import (  # noqa: E402
    TokenUsage,
    estimate_token_usage,
    estimate_usage_from_char_counts,
    normalize_provider_usage,
)

THRESHOLD = 0.90
SUITE_ID = "task-efficiency-100-v1"
Scenario = tuple[str, str, Callable[[], None]]
DEFAULT_BASELINE_USAGE = TokenUsage(900, 100)
DEFAULT_CURRENT_USAGE = TokenUsage(500, 100)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _nested_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_nested_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_nested_keys(item) for item in value))
    return set()


def _context(**updates) -> dict:
    value = {
        "experiment_id": "exp-fixed",
        "task_fingerprint": "fingerprint-fixed",
        "repo_commit": "abc123",
        "provider": "openai",
        "model": "gpt-5",
        "tool_policy": "tools-v1",
        "verification_policy": "proof-v1",
        "sample_count": 100,
    }
    value.update(updates)
    return value


def _start(
    store: TaskRunStore,
    root: Path,
    task_id: str,
    *,
    context: dict | None = None,
    variant: str = "",
) -> dict:
    return store.start_task(
        task_id,
        project=root.name,
        objective="Fixed evaluator task",
        project_root=root,
        base_commit="abc123",
        comparison_context=context,
        variant=variant,
    )


def _temporary_check(check: Callable[[Path], None]) -> None:
    with tempfile.TemporaryDirectory(prefix="flyto-task-eval-") as directory:
        check(Path(directory))


def _provider_scenarios() -> list[Scenario]:
    cases = [
        ("openai", {"input_tokens": 100, "output_tokens": 20}, (100, 20, 0, 0)),
        ("openai", {"prompt_tokens": 80, "completion_tokens": 15}, (80, 15, 0, 0)),
        ("azure-openai", {"input_tokens": 50, "output_tokens": 9}, (50, 9, 0, 0)),
        ("azure_openai", {"prompt_tokens": 31, "completion_tokens": 7}, (31, 7, 0, 0)),
        (
            "openai",
            {"input_tokens": 70, "output_tokens": 8, "cached_input_tokens": 11},
            (70, 8, 11, 0),
        ),
        ("openai", {"input_tokens": 70, "output_tokens": 8, "reasoning_tokens": 4}, (70, 8, 0, 4)),
        (
            "openai",
            {"input_tokens": 70, "output_tokens": 8, "input_tokens_details": {"cached_tokens": 12}},
            (70, 8, 12, 0),
        ),
        (
            "openai",
            {
                "input_tokens": 70,
                "output_tokens": 8,
                "output_tokens_details": {"reasoning_tokens": 5},
            },
            (70, 8, 0, 5),
        ),
        ("anthropic", {"input_tokens": 90, "output_tokens": 18}, (90, 18, 0, 0)),
        ("claude", {"input_tokens": 91, "output_tokens": 19}, (91, 19, 0, 0)),
        (
            "anthropic",
            {"input_tokens": 90, "output_tokens": 18, "cache_read_input_tokens": 10},
            (90, 18, 10, 0),
        ),
        (
            "anthropic",
            {"input_tokens": 90, "output_tokens": 18, "cache_creation_input_tokens": 6},
            (90, 18, 6, 0),
        ),
        (
            "anthropic",
            {
                "input_tokens": 90,
                "output_tokens": 18,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 6,
            },
            (90, 18, 16, 0),
        ),
        ("gemini", {"prompt_token_count": 77, "candidates_token_count": 14}, (77, 14, 0, 0)),
        ("google", {"prompt_token_count": 78, "candidates_token_count": 15}, (78, 15, 0, 0)),
        ("google-gemini", {"prompt_token_count": 79, "candidates_token_count": 16}, (79, 16, 0, 0)),
        (
            "gemini",
            {
                "prompt_token_count": 77,
                "candidates_token_count": 14,
                "cached_content_token_count": 9,
            },
            (77, 14, 9, 0),
        ),
        (
            "gemini",
            {"prompt_token_count": 77, "candidates_token_count": 14, "thoughts_token_count": 3},
            (77, 14, 0, 3),
        ),
        ("generic", {"input_tokens": 60, "output_tokens": 12}, (60, 12, 0, 0)),
        (
            "custom",
            {"prompt_tokens": 61, "completion_tokens": 13, "cached_tokens": 7},
            (61, 13, 7, 0),
        ),
    ]
    scenarios: list[Scenario] = []
    for number, (provider, metadata, expected) in enumerate(cases, 1):

        def check(provider=provider, metadata=metadata, expected=expected) -> None:
            usage = normalize_provider_usage(provider, metadata)
            actual = (
                usage.input_tokens,
                usage.output_tokens,
                usage.cached_input_tokens,
                usage.reasoning_tokens,
            )
            _require(actual == expected, f"normalized {actual}, expected {expected}")
            _require(usage.source == "reported", "provider counts must be reported")

        scenarios.append((f"provider-{number:02d}", "provider_usage", check))
    return scenarios


def _estimation_scenarios() -> list[Scenario]:
    scenarios: list[Scenario] = []
    counts = [
        (0, 0),
        (1, 0),
        (4, 1),
        (5, 5),
        (39, 13),
        (40, 16),
        (41, 17),
        (400, 80),
        (401, 81),
        (4096, 1024),
    ]
    for number, (input_chars, output_chars) in enumerate(counts, 1):

        def check(input_chars=input_chars, output_chars=output_chars) -> None:
            usage = estimate_usage_from_char_counts(input_chars, output_chars)
            _require(usage.input_tokens == (input_chars + 3) // 4, "input estimate")
            _require(usage.output_tokens == (output_chars + 3) // 4, "output estimate")
            _require(usage.estimator == "heuristic:chars-v1", "estimator identity")

        scenarios.append((f"estimate-{number:02d}", "estimation", check))
    texts = ["hello", "hello world", "世界", "hello 世界", "def run(value): return value"]
    for number, sample in enumerate(texts, 11):

        def check(sample=sample) -> None:
            first = estimate_token_usage(sample, "done", prefer_tiktoken=False)
            second = estimate_token_usage(sample, "done", prefer_tiktoken=False)
            _require(first == second, "heuristic must be deterministic")
            _require(first.total_tokens > 0, "non-empty text must have tokens")
            _require(sample not in json.dumps(first.to_dict()), "text must not be retained")

        scenarios.append((f"estimate-{number:02d}", "estimation", check))
    return scenarios


def _continuity_case(number: int, root: Path) -> None:
    path = root / ".flyto-index" / "task-runs.sqlite"
    if number == 1:
        _require(read_task_continuity(root)["status"] == "closed", "new project closed")
        _require(not path.parent.exists(), "read must not create directory")
        return
    store = TaskRunStore(path)
    run = _start(store, root, f"continuity-{number}")
    if number in {2, 3, 4, 5}:
        store.update_continuity(
            run["run_id"],
            remaining_steps=[f"step-{number}"],
            changed_paths=[f"src/file_{number}.py"],
            next_action="Continue verification",
        )
        state = store.continuity(project=root.name)
        _require(state["handoff_required"], "unfinished state must request handoff")
    elif number == 6:
        store.finish_task(run["run_id"], success=True, verification={"pass": True})
        _require(store.continuity(project=root.name)["status"] == "closed", "passed closes")
    elif number == 7:
        try:
            store.update_continuity(run["run_id"], changed_paths=["/etc/passwd"])
        except ValueError:
            return
        raise AssertionError("absolute paths must be rejected")
    elif number == 8:
        try:
            store.update_continuity(run["run_id"], changed_paths=["../secret"])
        except ValueError:
            return
        raise AssertionError("parent paths must be rejected")
    elif number == 9:
        secret = store.start_task(
            "secret",
            project=root.name,
            objective="password=unsafe /Users/alice/private.py",
            project_root=root,
            base_commit="abc123",
        )
        _require("unsafe" not in secret["objective"], "assigned secret redacted")
        _require("/Users/alice" not in secret["objective"], "home path redacted")
    elif number == 10:
        fenced = store.start_task(
            "fenced",
            project=root.name,
            objective="review ```private source``` safely",
            project_root=root,
            base_commit="abc123",
        )
        _require("private source" not in fenced["objective"], "code fence removed")
    elif number == 11:
        usage = TokenUsage(10, 2)
        first = store.record_usage(run["run_id"], usage, provider="p", model="m", event_id="same")
        second = store.record_usage(run["run_id"], usage, provider="p", model="m", event_id="same")
        _require(first["recorded"] and not second["recorded"], "event idempotency")
        other = _start(store, root, "same-event-other-run")
        cross_run = store.record_usage(
            other["run_id"], usage, provider="p", model="m", event_id="same"
        )
        _require(cross_run["recorded"], "event IDs are scoped to one run")
    elif number == 12:
        columns = store.schema_columns()
        forbidden = {"prompt", "response", "source_code", "provider_metadata"}
        _require(forbidden.isdisjoint(columns["task_runs"]), "private task columns")
        _require(forbidden.isdisjoint(columns["usage_events"]), "private usage columns")
    elif number == 13:
        control = _start(store, root, "paired", context=_context(), variant="control")
        indexer = _start(store, root, "paired", context=_context(), variant="indexer")
        _require(control["run_id"] != indexer["run_id"], "variants need distinct runs")
    elif number == 14:
        other = _start(store, root, "other-active")
        _require(store.get_run(run["run_id"])["status"] == "superseded", "one active task")
        _require(other["status"] == "active", "latest remains active")
    elif number == 15:
        readonly = TaskRunStore(path, readonly=True)
        _require(readonly.get_run(run["run_id"])["task_id"] == run["task_id"], "readonly read")
    elif number == 16:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE task_runs SET expires_at = '2000-01-01T00:00:00Z' WHERE run_id = ?",
                (run["run_id"],),
            )
        _require(store.continuity(project=root.name)["status"] == "expired", "expired state")
    elif number == 17:
        for item in range(4):
            candidate = _start(store, root, f"old-{item}")
            store.finish_task(candidate["run_id"], success=True, verification={"pass": True})
        _start(store, root, "kept-active")
        result = store.prune(max_runs=2)
        _require(result["overflow_runs_deleted"] >= 2, "bounded history")
    elif number == 18:
        try:
            store.start_task("bad-ttl", project=root.name, objective="x", ttl_days=0)
        except ValueError:
            return
        raise AssertionError("invalid TTL must fail")
    elif number == 19:
        rejected = 0
        try:
            store.record_usage(
                run["run_id"], TokenUsage(1, 1), provider="p", model="m", tool_calls=-1
            )
        except ValueError:
            rejected += 1
        try:
            store.record_usage(run["run_id"], TokenUsage(-1, 1), provider="p", model="m")
        except ValueError:
            rejected += 1
        _require(rejected == 2, "negative counters must fail")
    elif number == 20:
        report = store.report(run["run_id"])
        _require(report["privacy"].startswith("normalized_counts_only"), "privacy declaration")
        forbidden = {"prompt", "response", "source_code", "provider_metadata"}
        _require(forbidden.isdisjoint(_nested_keys(report)), "report excludes raw content fields")


def _continuity_scenarios() -> list[Scenario]:
    return [
        (
            f"continuity-{number:02d}",
            "continuity_privacy",
            lambda number=number: _temporary_check(lambda root: _continuity_case(number, root)),
        )
        for number in range(1, 21)
    ]


def _paired_runs(
    root: Path,
    *,
    baseline_context: dict | None = None,
    current_context: dict | None = None,
    baseline_variant: str = "control",
    current_variant: str = "indexer",
    baseline_status: bool | None = True,
    current_status: bool | None = True,
    baseline_usage: TokenUsage | None = DEFAULT_BASELINE_USAGE,
    current_usage: TokenUsage | None = DEFAULT_CURRENT_USAGE,
    baseline_provider: str = "openai",
    current_provider: str = "openai",
) -> tuple[TaskRunStore, dict, dict]:
    store = TaskRunStore(root / "runs.sqlite")
    baseline = _start(
        store,
        root,
        "paired",
        context=baseline_context if baseline_context is not None else _context(),
        variant=baseline_variant,
    )
    if baseline_usage is not None:
        store.record_usage(
            baseline["run_id"], baseline_usage, provider=baseline_provider, model="gpt-5"
        )
    if baseline_status is not None:
        store.finish_task(
            baseline["run_id"],
            success=baseline_status,
            verification={"pass": baseline_status},
        )
    current = _start(
        store,
        root,
        "paired",
        context=current_context if current_context is not None else _context(),
        variant=current_variant,
    )
    if current_usage is not None:
        store.record_usage(
            current["run_id"], current_usage, provider=current_provider, model="gpt-5"
        )
    if current_status is not None:
        store.finish_task(
            current["run_id"],
            success=current_status,
            verification={"pass": current_status},
        )
    return store, baseline, current


def _comparison_case(number: int, root: Path) -> None:
    if number <= 8:
        before = 1000 + number * 20
        after = 600 + number * 10
        source = "reported" if number <= 4 else "estimated"
        estimator = "" if source == "reported" else "heuristic:chars-v1"
        store, baseline, current = _paired_runs(
            root,
            baseline_usage=TokenUsage(before - 100, 100, source=source, estimator=estimator),
            current_usage=TokenUsage(after - 100, 100, source=source, estimator=estimator),
        )
        result = store.compare_runs(baseline["run_id"], current["run_id"])
        _require(result["available"], "valid pair must compare")
        expected_claim = "measured_reduction" if source == "reported" else "estimated_reduction"
        _require(result["claim"] == expected_claim, "claim must disclose measurement")
        return
    kwargs: dict = {}
    expected = ""
    if number == 9:
        kwargs["baseline_context"] = {"experiment_id": "incomplete"}
        expected = "baseline_context_incomplete"
    elif number == 10:
        kwargs["current_context"] = _context(model="different")
        expected = "comparison_context_mismatch"
    elif number == 11:
        kwargs["current_context"] = _context(repo_commit="different")
        expected = "comparison_context_mismatch"
    elif number == 12:
        kwargs["current_context"] = _context(tool_policy="different")
        expected = "comparison_context_mismatch"
    elif number == 13:
        kwargs["current_context"] = _context(verification_policy="different")
        expected = "comparison_context_mismatch"
    elif number == 14:
        kwargs["current_variant"] = "control"
        expected = "paired_variants_required"
    elif number == 15:
        kwargs["baseline_status"] = False
        expected = "both_runs_must_pass_verification"
    elif number == 16:
        kwargs["current_status"] = False
        expected = "both_runs_must_pass_verification"
    elif number == 17:
        kwargs["baseline_usage"] = None
        expected = "usage_evidence_missing"
    elif number == 18:
        kwargs["current_provider"] = "different"
        expected = "measurement_method_mismatch"
    elif number == 19:
        kwargs["baseline_usage"] = TokenUsage(0, 0)
        expected = "baseline_tokens_must_be_positive"
    elif number == 20:
        store = TaskRunStore(root / "runs.sqlite")
        result = store.compare_runs("missing-a", "missing-b")
        _require(result["reason"] == "task_run_not_found", "missing runs refused")
        return
    store, baseline, current = _paired_runs(root, **kwargs)
    result = store.compare_runs(baseline["run_id"], current["run_id"])
    _require(not result["available"], "invalid pair must not compare")
    _require(result["reason"] == expected, f"expected {expected}, got {result['reason']}")


def _comparison_scenarios() -> list[Scenario]:
    return [
        (
            f"comparison-{number:02d}",
            "honest_comparison",
            lambda number=number: _temporary_check(lambda root: _comparison_case(number, root)),
        )
        for number in range(1, 21)
    ]


def _sample_report() -> dict:
    return {
        "run": {
            "task_id": "task-1",
            "run_id": "run-1",
            "project": "demo",
            "status": "passed",
            "variant": "indexer",
        },
        "usage": {
            "input_tokens": 500,
            "output_tokens": 100,
            "total_tokens": 600,
            "sources": ["reported"],
            "tool_calls": 8,
            "duration_ms": 1200,
        },
        "efficiency": {"verified_successes_per_1000_tokens": 1.666667},
        "comparison": {
            "available": True,
            "claim": "measured_reduction",
            "before_tokens": 1000,
            "after_tokens": 600,
            "saved_tokens": 400,
            "reduction_percent": 40.0,
            "quality_regression": False,
        },
    }


def _report_case(number: int) -> None:
    report = _sample_report()
    if number == 1:
        _require("measured_reduction" in render_usage_report(report, "table"), "table claim")
    elif number == 2:
        _require(
            json.loads(render_usage_report(report, "json"))["run"]["status"] == "passed", "JSON"
        )
    elif number == 3:
        report["run"]["task_id"] = "=FORMULA()"
        rows = list(csv.DictReader(io.StringIO(render_usage_report(report, "csv"))))
        _require(rows[0]["total_tokens"] == "600", "CSV")
        _require(rows[0]["task_id"].startswith("'="), "CSV formula defense")
    elif number == 4:
        _require(render_usage_report(report, "html").startswith("<!doctype html>"), "HTML")
    elif number in range(5, 10):
        attacks = ["<script>x</script>", "<img src=x>", "A&B", '"quoted"', "'single'"]
        attack = attacks[number - 5]
        report["run"]["task_id"] = attack
        rendered = render_usage_report(report, "html")
        _require(html.escape(attack) in rendered, "HTML escaping")
        _require(attack not in rendered or attack == html.escape(attack), "raw injection")
    elif number == 10:
        _require(
            "not needed"
            in render_task_status({"continuity": {"status": "closed", "handoff_required": False}}),
            "closed status",
        )
    elif number == 11:
        payload = {
            "continuity": {
                "status": "needs_handoff",
                "handoff_required": True,
                "remaining": ["verify"],
            }
        }
        _require(
            "required before switching AI tools" in render_task_status(payload), "handoff reminder"
        )
    elif number == 12:
        payload = {
            "continuity": {"status": "active", "handoff_required": False, "objective": "finish"}
        }
        _require("finish" in render_task_status(payload), "objective status")
    elif number == 13:
        payload = {"continuity": {"status": "active", "handoff_required": False}}
        _require(
            json.loads(render_task_status(payload, as_json=True))["continuity"]["status"]
            == "active",
            "status JSON",
        )
    elif number == 14:
        report["comparison"] = {"available": False, "reason": "comparison_context_mismatch"}
        rendered = render_usage_report(report, "table")
        _require("unavailable" in rendered and "Saved" not in rendered, "no false saving")
    elif number == 15:
        try:
            render_usage_report(report, "pdf")
        except ValueError:
            return
        raise AssertionError("unsupported format must fail")


def _report_scenarios() -> list[Scenario]:
    return [
        (
            f"report-{number:02d}",
            "portable_reporting",
            lambda number=number: _report_case(number),
        )
        for number in range(1, 16)
    ]


def _cli_case(number: int, root: Path) -> None:
    parser = build_parser()
    if number == 1:
        _require(parser.parse_args(["task-status"]).command == "task-status", "status parser")
        return
    if number == 2:
        args = parser.parse_args(["task-status", str(root), "--json"])
        _require(
            json.loads(execute_task_status(args))["continuity"]["status"] == "closed", "new status"
        )
        _require(not (root / ".flyto-index").exists(), "status is read-only")
        return
    store = TaskRunStore(default_task_db(root))
    run = _start(store, root, f"cli-{number}")
    if number == 3:
        args = parser.parse_args(
            [
                "usage-record",
                run["run_id"],
                str(root),
                "--provider",
                "openai",
                "--model",
                "gpt-5",
                "--usage",
                '{"input_tokens":10,"output_tokens":2}',
            ]
        )
        _require(execute_usage_record(args)["recorded"], "provider CLI")
    elif number == 4:
        args = parser.parse_args(
            [
                "usage-record",
                run["run_id"],
                str(root),
                "--provider",
                "local",
                "--model",
                "unknown",
                "--estimated-input-chars",
                "40",
                "--estimated-output-chars",
                "8",
            ]
        )
        execute_usage_record(args)
        _require(store.aggregate_usage(run["run_id"])["total_tokens"] == 12, "estimate CLI")
    elif number in {5, 6, 7, 8}:
        store.record_usage(run["run_id"], TokenUsage(10, 2), provider="openai", model="gpt-5")
        formats = {5: "table", 6: "json", 7: "csv", 8: "html"}
        format_name = formats[number]
        args = parser.parse_args(
            ["usage-report", str(root), "--task", run["run_id"], "--format", format_name]
        )
        rendered = execute_usage_report(args)
        _require(bool(rendered.strip()), f"{format_name} CLI report")
    elif number == 9:
        store.record_usage(run["run_id"], TokenUsage(10, 2), provider="openai", model="gpt-5")
        output = root / "report.html"
        args = parser.parse_args(
            [
                "usage-report",
                str(root),
                "--task",
                run["run_id"],
                "--format",
                "html",
                "--output",
                str(output),
            ]
        )
        execute_usage_report(args)
        _require(output.read_text(encoding="utf-8").startswith("<!doctype html>"), "output file")
    elif number == 10:
        args = parser.parse_args(["task-status", str(root), "--all", "--json"])
        payload = json.loads(execute_task_status(args))
        _require(len(payload["runs"]) == 1, "recent runs")


def _cli_scenarios() -> list[Scenario]:
    return [
        (
            f"cli-{number:02d}",
            "cli_contract",
            lambda number=number: _temporary_check(lambda root: _cli_case(number, root)),
        )
        for number in range(1, 11)
    ]


def build_scenarios() -> list[Scenario]:
    scenarios = (
        _provider_scenarios()
        + _estimation_scenarios()
        + _continuity_scenarios()
        + _comparison_scenarios()
        + _report_scenarios()
        + _cli_scenarios()
    )
    if len(scenarios) != 100:
        raise AssertionError(
            f"fixed suite must contain exactly 100 scenarios, got {len(scenarios)}"
        )
    if len({scenario[0] for scenario in scenarios}) != 100:
        raise AssertionError("fixed suite scenario IDs must be unique")
    return scenarios


def evaluate() -> dict:
    results = []
    for scenario_id, category, check in build_scenarios():
        try:
            check()
            results.append({"id": scenario_id, "category": category, "passed": True})
        except Exception as exc:
            results.append(
                {
                    "id": scenario_id,
                    "category": category,
                    "passed": False,
                    "failure": f"{type(exc).__name__}: {exc}"[:300],
                }
            )
    passed = sum(1 for result in results if result["passed"])
    category_totals = Counter(result["category"] for result in results)
    category_passed = Counter(result["category"] for result in results if result["passed"])
    categories = {
        category: {
            "passed": category_passed[category],
            "total": total,
            "success_rate": round(category_passed[category] / total, 4),
        }
        for category, total in sorted(category_totals.items())
    }
    fingerprint_payload = [
        (result["id"], result["category"], result["passed"]) for result in results
    ]
    return {
        "schema_version": 1,
        "suite_id": SUITE_ID,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "success_rate": round(passed / len(results), 4),
        "required_success_rate": THRESHOLD,
        "pass": passed / len(results) >= THRESHOLD,
        "evidence_fingerprint": hashlib.sha256(
            json.dumps(fingerprint_payload, separators=(",", ":")).encode()
        ).hexdigest(),
        "categories": categories,
        "scenarios": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Exit non-zero below 90%")
    parser.add_argument("--json", action="store_true", help="Print JSON evidence")
    parser.add_argument("--output", help="Write deterministic JSON evidence")
    args = parser.parse_args()
    result = evaluate()
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    if args.json:
        print(rendered, end="")
    else:
        print(
            f"{result['passed']}/{result['total']} scenarios passed "
            f"({result['success_rate'] * 100:.1f}%; required {THRESHOLD * 100:.0f}%)"
        )
        for category, evidence in result["categories"].items():
            print(f"  {category}: {evidence['passed']}/{evidence['total']}")
        if result["failed"]:
            for scenario in result["scenarios"]:
                if not scenario["passed"]:
                    print(f"  FAIL {scenario['id']}: {scenario['failure']}")
    return 1 if args.check and not result["pass"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
