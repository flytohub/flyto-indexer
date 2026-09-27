"""Deterministic risk and project-signal scoring for task analysis.

This module computes evidence-backed dimensions only; it does not resolve edit
authority, build execution plans, or gate task phases.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

try:
    from .references import (
        find_references, impact_analysis, edit_impact_preview,
        cross_project_impact, dependency_graph,
    )
    from .code_info import find_test_file
    from ..index_store import load_index
    from ..quality import code_health_score
except ImportError:
    from tools.references import (
        find_references, impact_analysis, edit_impact_preview,
        cross_project_impact, dependency_graph,
    )
    from tools.code_info import find_test_file
    from index_store import load_index
    from quality import code_health_score


def _is_test_path(path: str) -> bool:
    """Return whether a canonical repository path names a test source file."""
    parts = tuple(part for part in path.split("/") if part)
    if not parts:
        return False
    if any(part in {"tests", "__tests__"} for part in parts[:-1]):
        return True

    name = parts[-1]
    if name.endswith("_test.go"):
        return True
    if name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")):
        return True
    stem, separator, suffix = name.rpartition(".")
    return (
        bool(separator)
        and suffix in {"js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts"}
        and (stem.endswith(".test") or stem.endswith(".spec"))
    )

def _score_to_level(score: float) -> str:
    """Convert 0-10 score to low/medium/high level."""
    if score >= 7.0:
        return "high"
    elif score >= 4.0:
        return "medium"
    return "low"

def _overall_risk(max_score: float) -> str:
    """Convert max dimension score to overall risk label."""
    if max_score >= 8.0:
        return "high"
    elif max_score >= 5.0:
        return "moderate"
    elif max_score >= 2.0:
        return "low"
    return "safe"

def _score_blast_radius(resolved: List[dict]) -> dict:
    """How many symbols/files/projects are affected."""
    total_affected = 0
    affected_files = set()
    affected_projects = set()
    evidence_items = []

    for target in resolved:
        sid = target.get("symbol_id")
        if not sid:
            continue
        result = impact_analysis(sid)
        count = result.get("affected_count", 0)
        total_affected += count
        for a in result.get("affected", []):
            path = a.get("path", "")
            if path:
                affected_files.add(path)
            aid = a.get("id", "")
            if ":" in aid:
                affected_projects.add(aid.split(":")[0])
        evidence_items.append({
            "symbol": sid,
            "affected_count": count,
        })

    # Scoring curve
    if total_affected == 0:
        score = 0.0
    elif total_affected <= 3:
        score = 2.0
    elif total_affected <= 10:
        score = 4.0 + (total_affected - 4) * 0.3
    elif total_affected <= 20:
        score = 6.0 + (total_affected - 11) * 0.2
    elif total_affected <= 50:
        score = 8.0 + (total_affected - 21) * 0.07
    else:
        score = 10.0
    score = round(min(score, 10.0), 1)

    rationale = f"{total_affected} affected symbols across {len(affected_files)} files"
    if affected_projects:
        rationale += f" in {len(affected_projects)} project(s)"

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": rationale,
        "evidence": {
            "affected_symbols": total_affected,
            "affected_files": len(affected_files),
            "affected_projects": sorted(affected_projects),
        },
    }

def _score_breaking_risk(resolved: List[dict], intent: str) -> dict:
    """Likelihood of breaking existing callers."""
    total_call_sites = 0
    max_risk = "safe"
    risk_order = {"safe": 0, "low": 1, "moderate": 2, "high": 3}
    has_public_api = False
    signature_changes = 0

    change_type_map = {
        "refactor": "signature_change",
        "bugfix": "modify",
        "feature": "modify",
        "cleanup": "delete",
        "migration": "rename",
    }
    change_type = change_type_map.get(intent, "modify")

    for target in resolved:
        sid = target.get("symbol_id")
        if not sid:
            continue
        result = edit_impact_preview(sid, change_type=change_type)
        sites = result.get("total_call_sites", 0)
        total_call_sites += sites
        risk = result.get("risk", "safe")
        if risk_order.get(risk, 0) > risk_order.get(max_risk, 0):
            max_risk = risk
        if sites > 0:
            has_public_api = True
        if change_type in ("signature_change", "rename", "delete"):
            signature_changes += 1

    # Score
    risk_scores = {"safe": 0.0, "low": 3.0, "moderate": 6.0, "high": 8.0}
    score = risk_scores.get(max_risk, 0.0)
    if total_call_sites > 20:
        score = min(score + 2.0, 10.0)
    elif total_call_sites > 10:
        score = min(score + 1.0, 10.0)
    score = round(score, 1)

    rationale_parts = []
    if has_public_api:
        rationale_parts.append("touches public API")
    if total_call_sites > 0:
        rationale_parts.append(f"{total_call_sites} call site(s)")
    if signature_changes > 0:
        rationale_parts.append(f"{signature_changes} signature change(s) detected")
    rationale = "; ".join(rationale_parts) if rationale_parts else "No breaking risk detected"

    # Reduce breaking risk for private/internal symbols
    all_private = all(
        t.get("name", "").startswith("_") or t.get("type") == "unknown"
        for t in resolved
    )
    if all_private and score > 3.0:
        score = max(2.0, score * 0.4)
        rationale += "; targets are private/internal (risk reduced)"

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": rationale,
        "evidence": {
            "total_call_sites": total_call_sites,
            "max_risk_level": max_risk,
            "has_public_api": has_public_api,
            "signature_changes_detected": signature_changes,
        },
    }

def _score_test_risk(
    resolved: List[dict],
    *,
    find_references_fn=find_references,
    find_test_file_fn=find_test_file,
) -> dict:
    """Risk from insufficient test coverage on callers.

    Checks both the target files AND their callers for test coverage.
    A symbol with tests is still risky if its callers lack tests.
    HIGH score = HIGH risk (low coverage).
    """
    targets_with_tests = 0
    targets_without_tests = 0
    callers_with_tests = 0
    callers_without_tests = 0
    test_files = []
    checked_paths = set()

    for target in resolved:
        sid = target.get("symbol_id")
        path = target.get("path", "")
        target_project = sid.split(":", 1)[0] if sid and ":" in sid else None

        # Check target file test coverage
        target_key = (target_project, path)
        if path and target_key not in checked_paths:
            checked_paths.add(target_key)
            result = find_test_file_fn(path, project=target_project)
            test_file = result.get("test_file", "")
            if test_file:
                targets_with_tests += 1
                test_files.append(test_file)
            else:
                targets_without_tests += 1
        elif not path:
            targets_without_tests += 1

        # Check caller test coverage via find_references
        if sid:
            refs = find_references_fn(sid)
            for ref in refs.get("references", []):
                caller_path = ref.get("from_path", "")
                caller_symbol = ref.get("from_symbol", "")
                caller_project = (
                    caller_symbol.split(":", 1)[0]
                    if ":" in caller_symbol
                    else target_project
                )
                caller_key = (caller_project, caller_path)
                if not caller_path or caller_key in checked_paths:
                    continue
                checked_paths.add(caller_key)
                caller_test = find_test_file_fn(
                    caller_path,
                    project=caller_project,
                )
                if caller_test.get("test_file"):
                    callers_with_tests += 1
                    test_files.append(caller_test["test_file"])
                else:
                    callers_without_tests += 1

    # Combined coverage: target coverage + caller coverage
    total_targets = targets_with_tests + targets_without_tests
    total_callers = callers_with_tests + callers_without_tests

    if total_targets == 0:
        target_ratio = 0.0
    else:
        target_ratio = targets_with_tests / total_targets

    if total_callers == 0:
        caller_ratio = 1.0  # No callers = no caller risk
    else:
        caller_ratio = callers_with_tests / total_callers

    # Weighted: target coverage matters more, but untested callers are risky too
    # 60% target coverage + 40% caller coverage
    combined_ratio = target_ratio * 0.6 + caller_ratio * 0.4

    # INVERTED: high coverage → low risk score
    score = round((1.0 - combined_ratio) * 10.0, 1)

    # Build rationale
    parts = []
    if total_targets > 0:
        parts.append(f"{targets_with_tests}/{total_targets} target files have tests")
    if total_callers > 0:
        parts.append(f"{callers_with_tests}/{total_callers} caller files have tests")

    if not parts:
        rationale = "No targets or callers to assess"
    elif combined_ratio == 0:
        rationale = "No test files found — very high risk. " + "; ".join(parts)
    elif combined_ratio < 0.5:
        rationale = "Low test coverage. " + "; ".join(parts)
    elif combined_ratio < 1.0:
        rationale = "Partial test coverage. " + "; ".join(parts)
    else:
        rationale = "Full test coverage. " + "; ".join(parts)

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": rationale,
        "evidence": {
            "target_files_tested": targets_with_tests,
            "target_files_untested": targets_without_tests,
            "caller_files_tested": callers_with_tests,
            "caller_files_untested": callers_without_tests,
            "test_files": sorted(set(test_files)),
        },
    }

def _score_cross_coupling(resolved: List[dict]) -> dict:
    """How many projects share the affected symbols.

    Uses find_references(symbol_id) to get precise cross-project callers,
    avoiding name-matching false positives from cross_project_impact(name).
    """
    all_affected_projects = set()
    cross_refs = []
    source_projects = set()

    for target in resolved:
        sid = target.get("symbol_id")
        if not sid:
            continue

        # Determine source project from symbol_id
        src_project = sid.split(":")[0] if ":" in sid else ""
        if src_project:
            source_projects.add(src_project)

        # Use find_references for precise symbol-level cross-project refs
        refs = find_references(sid)
        for ref in refs.get("references", []):
            caller_id = ref.get("from_symbol", "")
            if ":" not in caller_id:
                continue
            caller_project = caller_id.split(":")[0]

            # Only count cross-project references
            if caller_project and caller_project != src_project:
                all_affected_projects.add(caller_project)
                cross_refs.append({
                    "caller": caller_id,
                    "caller_project": caller_project,
                    "source_project": src_project,
                })

    num_projects = len(all_affected_projects)
    total_cross_refs = len(cross_refs)

    if num_projects == 0:
        score = 0.0
    elif num_projects == 1:
        score = 3.0 + min(total_cross_refs * 0.5, 2.0)
    elif num_projects == 2:
        score = 5.0 + min(total_cross_refs * 0.3, 2.0)
    else:
        score = 7.0 + min(num_projects * 0.5, 3.0)
    score = round(min(score, 10.0), 1)

    if num_projects == 0:
        rationale = "No cross-project coupling detected"
    else:
        rationale = f"Shared by {num_projects} project(s) with {total_cross_refs} cross-reference(s)"

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": rationale,
        "evidence": {
            "shared_by_projects": sorted(all_affected_projects),
            "total_cross_refs": total_cross_refs,
            "source_projects": sorted(source_projects),
        },
    }

def _score_complexity(resolved: List[dict]) -> dict:
    """Dependency depth + code complexity of targets."""
    max_depth = 0
    total_deps = 0
    total_dependents = 0
    complex_functions_count = 0

    for target in resolved:
        sid = target.get("symbol_id")
        path = target.get("path", "")
        if not path and not sid:
            continue

        dep_result = dependency_graph(
            file_path=path if path else None,
            symbol_id=sid if not path else None,
            direction="both",
            max_depth=3,
        )
        summary = dep_result.get("summary", {})
        imports_count = summary.get("imports_count", 0)
        dependents_count = summary.get("dependents_count", 0)
        total_deps += imports_count
        total_dependents += dependents_count
        depth = min(imports_count, 10)
        if depth > max_depth:
            max_depth = depth

        # Check symbol line count
        if sid:
            index = load_index()
            sym = index.get("symbols", {}).get(sid, {})
            lines = sym.get("end_line", 0) - sym.get("start_line", 0)
            if lines > 50:
                complex_functions_count += 1

    dep_score = min(total_deps * 0.5, 5.0)
    depth_score = min(max_depth * 0.8, 3.0)
    complex_bonus = min(complex_functions_count * 1.0, 2.0)
    score = round(min(dep_score + depth_score + complex_bonus, 10.0), 1)

    rationale = f"Dependency depth {max_depth}, {total_deps} imports, {total_dependents} dependents"
    if complex_functions_count > 0:
        rationale += f", {complex_functions_count} complex function(s)"

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": rationale,
        "evidence": {
            "dependency_depth": max_depth,
            "total_imports": total_deps,
            "total_dependents": total_dependents,
            "complex_functions": complex_functions_count,
        },
    }

def _score_rollback_difficulty(
    blast: dict, breaking: dict, coupling: dict, complexity: dict,
) -> dict:
    """Signal-based rollback difficulty scoring.

    Instead of a weighted average, counts discrete risk signals:
    - public_api_change: exported symbol with callers
    - multi_project: affects more than 1 project
    - many_consumers: >10 call sites
    - high_breaking: breaking_risk is high
    - high_coupling: cross_coupling is high
    - deep_dependency: complexity is high

    Each signal contributes to the score. More signals = harder rollback.
    """
    signals = []

    # Signal: public API change
    if breaking.get("evidence", {}).get("has_public_api", False):
        signals.append("public_api_change")

    # Signal: multi-project impact
    affected_projects = blast.get("evidence", {}).get("affected_projects", [])
    coupled_projects = coupling.get("evidence", {}).get("shared_by_projects", [])
    all_projects = set(affected_projects) | set(coupled_projects)
    if len(all_projects) > 1:
        signals.append("multi_project")

    # Signal: many consumers
    call_sites = breaking.get("evidence", {}).get("total_call_sites", 0)
    if call_sites > 10:
        signals.append("many_consumers")

    # Signal: high breaking risk
    if breaking.get("level") == "high":
        signals.append("high_breaking")

    # Signal: high cross-coupling
    if coupling.get("level") == "high":
        signals.append("high_coupling")

    # Signal: deep dependency chain
    if complexity.get("level") == "high":
        signals.append("deep_dependency")

    # Score: each signal adds ~2 points, capped at 10
    num_signals = len(signals)
    if num_signals == 0:
        score = 0.0
    elif num_signals == 1:
        score = 3.0
    elif num_signals == 2:
        score = 5.0
    elif num_signals == 3:
        score = 7.0
    elif num_signals == 4:
        score = 8.5
    else:
        score = min(9.0 + (num_signals - 5) * 0.5, 10.0)
    score = round(score, 1)

    # Rationale from signals
    signal_descriptions = {
        "public_api_change": "Public API change — consumers may have adapted",
        "multi_project": f"Affects {len(all_projects)} projects — coordinated rollback needed",
        "many_consumers": f"{call_sites} call sites — wide rollback surface",
        "high_breaking": "High breaking risk makes rollback dangerous",
        "high_coupling": "Cross-project coupling requires coordinated rollback",
        "deep_dependency": "Deep dependency chain complicates rollback",
    }

    rationale_parts = [signal_descriptions[s] for s in signals]
    if not rationale_parts:
        rationale_parts = ["Low rollback risk"]

    return {
        "score": score,
        "level": _score_to_level(score),
        "rationale": "; ".join(rationale_parts),
        "evidence": {
            "signals": signals,
            "signal_count": num_signals,
        },
    }

def _compute_project_signals(resolved: List[dict], project: str = None) -> dict:
    """Compute project signals: health score + real test file sampling.

    - health_score: from code_health_score (zero extra I/O)
    - test_maturity: actual find_test_file sampling on unique project paths (max 30)
    - complexity_baseline: from code_health_score complexity dimension
    """
    index = load_index()
    symbols = index.get("symbols", {})

    # Determine project from resolved targets
    target_project = project
    if not target_project:
        for t in resolved:
            sid = t.get("symbol_id") or ""
            if ":" in sid:
                target_project = sid.split(":")[0]
                break

    # --- Health score (zero extra I/O) ---
    health = code_health_score(project=target_project)

    if health.get("error"):
        health_signal = {"score": 0, "grade": "N/A", "basis": "No data available"}
        complexity_baseline = {"score": 0.0, "basis": "No data available"}
    else:
        total_score = health.get("score", 0)
        grade = health.get("grade", "N/A")
        health_signal = {
            "score": total_score,
            "grade": grade,
            "basis": f"Code health {total_score}/100 (grade {grade})",
        }
        breakdown = health.get("breakdown", {})
        complexity_raw = breakdown.get("complexity", {}).get("score", 25)
        complexity_detail = breakdown.get("complexity", {}).get("detail", "")
        # Invert: health score gives 25=good, 0=bad → we want 0=simple, 10=complex
        complexity_baseline = {
            "score": round((25 - complexity_raw) / 25 * 10, 1),
            "basis": complexity_detail or f"Complexity score {complexity_raw}/25",
        }

    # --- Test maturity: real sampling with path dedup ---
    tested = 0
    sampled = 0
    seen_paths = set()
    SAMPLE_CAP = 30

    for sid, sym in symbols.items():
        if target_project and not sid.startswith(target_project + ":"):
            continue
        path = sym.get("path", "")
        if not path or path in seen_paths:
            continue
        # Skip test files themselves
        if "/test" in path.lower() or path.lower().startswith("test"):
            continue
        seen_paths.add(path)
        sampled += 1
        if sampled > SAMPLE_CAP:
            break
        result = find_test_file(path)
        if result.get("test_file"):
            tested += 1

    if sampled > 0:
        maturity_score = round(tested / sampled * 10, 1)
        basis = f"{tested}/{sampled} sampled source files have test files"
    else:
        maturity_score = 0.0
        basis = "No source files to sample"

    return {
        "health_score": health_signal,
        "test_maturity": {
            "score": maturity_score,
            "basis": basis,
        },
        "complexity_baseline": complexity_baseline,
    }

def _compute_index_confidence(resolved: List[dict]) -> dict:
    """Assess how trustworthy the index data is for the resolved targets.

    Checks:
    - reverse_index coverage: do resolved symbols have entries?
    - dependency resolution: are deps resolved or just name-based?
    - symbol completeness: do symbols have path, start_line, end_line?

    Returns score 0-10 (10 = fully reliable) and list of warnings.
    """
    index = load_index()
    symbols = index.get("symbols", {})
    reverse_index = index.get("reverse_index", {})
    dependencies = index.get("dependencies", {})

    total_checks = 0
    passed_checks = 0
    warnings = []

    for target in resolved:
        sid = target.get("symbol_id")
        if not sid:
            continue

        # Check 1: symbol exists in index
        total_checks += 1
        sym = symbols.get(sid)
        if sym:
            passed_checks += 1
        else:
            warnings.append(f"Symbol {sid} not found in index")
            continue

        # Check 2: symbol has complete metadata (path + lines)
        total_checks += 1
        if sym.get("path") and sym.get("start_line") is not None:
            passed_checks += 1
        else:
            warnings.append(f"Symbol {sid} has incomplete metadata")

        # Check 3: reverse_index has entry for this symbol
        total_checks += 1
        if sid in reverse_index or sym.get("name", "") in reverse_index:
            passed_checks += 1
        else:
            # Not having reverse_index entries could mean 0 callers (ok)
            # or missing data (bad). Check if deps reference this symbol.
            has_dep_refs = any(
                dep.get("metadata", {}).get("resolved_target") == sid
                for dep in dependencies.values()
            )
            if has_dep_refs:
                # Deps reference it but reverse_index doesn't — data gap
                warnings.append(f"Symbol {sid} referenced in deps but missing from reverse_index")
            else:
                passed_checks += 1  # Likely just has no callers

    # Check overall index stats
    total_checks += 1
    total_syms = len(symbols)
    reverse_entries = len(reverse_index)
    if total_syms > 0 and reverse_entries / total_syms > 0.1:
        passed_checks += 1
    elif total_syms > 0:
        warnings.append(f"Low reverse_index coverage: {reverse_entries}/{total_syms} symbols")
    else:
        warnings.append("No symbols in index")

    if total_checks == 0:
        return {"score": 0.0, "level": "low", "warnings": ["No targets to assess"]}

    score = round(passed_checks / total_checks * 10, 1)
    level = "high" if score >= 8 else "medium" if score >= 5 else "low"

    return {
        "score": score,
        "level": level,
        "checks_passed": passed_checks,
        "checks_total": total_checks,
        "warnings": warnings,
    }

