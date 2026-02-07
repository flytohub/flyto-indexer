"""
Generate .flyto/tags/symbol_tags.jsonl — unified tag system.

Tag kinds:
  - dead_code: symbol has 0 references after 4-stage filtering
  - tdd_covered_by: source symbol is called/imported by test files
  - tdd_uncovered: source symbol has no test coverage

Philosophy:
  - Tags are hash-bound (basedOn.contentHash) for staleness tracking
  - JSONL format for incremental append
  - Same stale rules as descriptions.jsonl
"""

import json
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

from .models import ProjectIndex, SymbolType, DependencyType


# Symbol types that should be referenced (dead code candidates).
SHOULD_BE_REFERENCED = {
    SymbolType.FUNCTION,
    SymbolType.METHOD,
    SymbolType.COMPOSABLE,
    SymbolType.COMPONENT,
    SymbolType.CLASS,
}

# Entry point patterns (never dead code).
ENTRY_POINT_PATTERNS = [
    "main", "index", "app", "App", "Main",
    "__init__", "setup", "teardown",
    "test_", "Test", "_test",
    "register", "init", "configure",
    "handle", "route", "endpoint",
    "do_GET", "do_POST", "do_PUT", "do_DELETE",
    "do_HEAD", "do_OPTIONS", "do_PATCH",
]

# Vue/React lifecycle methods (never dead code).
LIFECYCLE_METHODS = {
    "created", "mounted", "updated", "destroyed",
    "beforeCreate", "beforeMount", "beforeUpdate", "beforeDestroy",
    "onMounted", "onUnmounted", "onUpdated",
    "componentDidMount", "componentWillUnmount", "render",
    "setup", "data", "computed", "methods", "watch",
}

# Min lines to consider (skip tiny functions).
MIN_LINES = 5

# Test path patterns.
TEST_PATH_INDICATORS = ["/test", "/tests/", "/__tests__/", "/spec/"]
TEST_NAME_PATTERNS = ["test_", ".test.", ".spec.", "_test."]


def generate_tags(index: ProjectIndex) -> list[dict]:
    """Generate all tags for a project index.

    Returns list of tag dicts (one per JSONL line).
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tags = []

    # --- Dead Code ---
    dead_symbols = _find_dead_code(index)
    for sym_id, reason in dead_symbols.items():
        sym = index.symbols[sym_id]
        tags.append({
            "schemaVersion": 1,
            "kind": "dead_code",
            "targetType": "symbol",
            "symbolId": sym_id,
            "path": sym.path,
            "name": sym.name,
            "symbolKind": sym.symbol_type.value,
            "lines": sym.end_line - sym.start_line,
            "severity": "warning",
            "reason": reason,
            "basedOn": {"contentHash": sym.content_hash or ""},
            "generatedAt": now,
        })

    # --- TDD L0 Mapping (reverse_index) ---
    tdd_tags = _find_tdd_mapping(index)

    # --- TDD L1: Filename matching (test_X.py ↔ X.py) ---
    tdd_filename = _find_tdd_by_filename(index)
    for sym_id, test_refs in tdd_filename.items():
        if sym_id not in tdd_tags:
            tdd_tags[sym_id] = test_refs

    for sym_id, test_refs in tdd_tags.items():
        sym = index.symbols[sym_id]
        tags.append({
            "schemaVersion": 1,
            "kind": "tdd_covered_by",
            "targetType": "symbol",
            "symbolId": sym_id,
            "path": sym.path,
            "name": sym.name,
            "symbolKind": sym.symbol_type.value,
            "refs": test_refs,
            "basedOn": {"contentHash": sym.content_hash or ""},
            "generatedAt": now,
        })

    return tags


def compute_tag_stats(tags: list[dict], index: ProjectIndex) -> dict:
    """Compute tag statistics for summary.json."""
    dead_count = sum(1 for t in tags if t["kind"] == "dead_code")
    dead_lines = sum(t.get("lines", 0) for t in tags if t["kind"] == "dead_code")
    tdd_covered = sum(1 for t in tags if t["kind"] == "tdd_covered_by")

    # Count testable symbols (non-test, top-level, > MIN_LINES)
    testable = 0
    for sym in index.symbols.values():
        if sym.symbol_type not in SHOULD_BE_REFERENCED:
            continue
        if _is_test_path(sym.path):
            continue
        if (sym.end_line - sym.start_line) < MIN_LINES:
            continue
        testable += 1

    return {
        "dead_code": dead_count,
        "dead_code_lines": dead_lines,
        "tdd_covered": tdd_covered,
        "tdd_uncovered": max(0, testable - tdd_covered),
        "tdd_testable": testable,
    }


def write_tags(tags: list[dict], tags_dir: Path):
    """Write tags to symbol_tags.jsonl (overwrite, not append).

    Tags are regenerated on each index run, so we overwrite.
    Unlike descriptions.jsonl (AI-appended, never overwrite).
    """
    tags_dir.mkdir(parents=True, exist_ok=True)
    path = tags_dir / "symbol_tags.jsonl"
    lines = [json.dumps(t, ensure_ascii=False) for t in tags]
    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


# ---------------------------------------------------------------------------
# Dead Code Detection (ported from mcp_server.py find_dead_code)
# ---------------------------------------------------------------------------

def _find_dead_code(index: ProjectIndex) -> dict[str, str]:
    """Find dead code symbols. Returns {symbol_id: reason}."""

    # Stage 1: Collect all referenced names from dependencies
    referenced_names: set[str] = set()
    imported_files: set[str] = set()
    referenced_classes: set[str] = set()

    for dep in index.dependencies.values():
        if dep.dep_type == DependencyType.IMPORTS:
            names = dep.metadata.get("names", [])
            for name in names:
                referenced_names.add(name)
                if name and name[0].isupper():
                    referenced_classes.add(name)
            target = dep.target_id
            if target:
                imported_files.add(target)
                basename = target.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                imported_files.add(basename)
                if "." in target:
                    last_part = target.rsplit(".", 1)[-1]
                    imported_files.add(last_part)

        elif dep.dep_type == DependencyType.CALLS:
            target = dep.target_id
            if target and not target.startswith("__"):
                referenced_names.add(target)
                parts = target.split(".")
                for part in parts:
                    if part and len(part) > 2:
                        referenced_names.add(part)
                        if part[0].isupper():
                            referenced_classes.add(part)

    # Stage 2-4: Filter symbols
    dead: dict[str, str] = {}

    for sym_id, sym in index.symbols.items():
        if sym.symbol_type not in SHOULD_BE_REFERENCED:
            continue

        lines = sym.end_line - sym.start_line
        if lines < MIN_LINES:
            continue

        # Entry points
        if any(p in sym.name for p in ENTRY_POINT_PATTERNS):
            continue

        # Lifecycle methods
        if sym.name in LIFECYCLE_METHODS:
            continue

        # Vue template functions (hard to track @click handlers)
        if sym.symbol_type == SymbolType.FUNCTION and sym.path.endswith(".vue"):
            continue

        # Exported symbols
        if sym.exports:
            continue

        # Private methods (convention: _name but not __name)
        if sym.name.startswith("_") and not sym.name.startswith("__"):
            continue

        # Check if name is referenced
        if sym.name in referenced_names:
            continue

        # Method: also check method-only name and class name
        if sym.symbol_type == SymbolType.METHOD and "." in sym.name:
            method_only = sym.name.split(".")[-1]
            if method_only in referenced_names:
                continue
            class_name = sym.name.split(".")[0]
            if class_name in referenced_classes or class_name in referenced_names:
                continue

        # Class: check if class name is referenced
        if sym.symbol_type == SymbolType.CLASS and sym.name in referenced_classes:
            continue

        # File-level import check (for classes/components)
        file_basename = sym.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if file_basename in imported_files or sym.path in imported_files:
            continue

        # Composable: check if imported by any file
        if sym.symbol_type == SymbolType.COMPOSABLE:
            if _is_composable_imported(sym, file_basename, index):
                continue

        # Class/Component with matching filename: check if file is imported
        if sym.symbol_type in (SymbolType.CLASS, SymbolType.COMPONENT):
            if file_basename == sym.name:
                if _is_file_imported(sym, file_basename, index):
                    continue

        # Final check: reverse_index
        callers = index.reverse_index.get(sym_id, [])
        if sym.reference_count > 0 or len(callers) > 0:
            continue

        dead[sym_id] = f"ref_count=0, no callers, not in referenced_names"

    return dead


def _is_composable_imported(sym, file_basename: str, index: ProjectIndex) -> bool:
    """Check if a composable is imported by any file."""
    for dep in index.dependencies.values():
        if dep.dep_type == DependencyType.IMPORTS:
            target = dep.target_id
            names = dep.metadata.get("names", [])
            if sym.name in (target or "") or file_basename in (target or ""):
                return True
            if sym.name in names:
                return True
    return False


def _is_file_imported(sym, file_basename: str, index: ProjectIndex) -> bool:
    """Check if a file containing a class/component is imported."""
    for dep in index.dependencies.values():
        if dep.dep_type == DependencyType.IMPORTS:
            target = dep.target_id or ""
            if sym.name in target or file_basename in target:
                return True
    return False


# ---------------------------------------------------------------------------
# TDD L0 Mapping
# ---------------------------------------------------------------------------

def _find_tdd_mapping(index: ProjectIndex) -> dict[str, list[dict]]:
    """Find test-to-source mappings from reverse_index.

    L0 rule: if a symbol's caller is in a test file path,
    that symbol is "covered" by that test.

    Returns {source_symbol_id: [{testId, testPath}]}.
    """
    result: dict[str, list[dict]] = {}

    for sym_id, callers in index.reverse_index.items():
        if sym_id not in index.symbols:
            continue
        sym = index.symbols[sym_id]

        # Skip test symbols themselves
        if _is_test_path(sym.path):
            continue
        # Skip non-testable types
        if sym.symbol_type not in SHOULD_BE_REFERENCED:
            continue

        test_refs = []
        for caller_id in callers:
            # Parse caller path from symbol ID (project:path:type:name)
            parts = caller_id.split(":", 3)
            if len(parts) < 4:
                continue
            caller_path = parts[1]

            if _is_test_path(caller_path):
                test_refs.append({
                    "testId": caller_id,
                    "testPath": caller_path,
                })

        if test_refs:
            result[sym_id] = test_refs

    return result


def _find_tdd_by_filename(index: ProjectIndex) -> dict[str, list[dict]]:
    """Find test-source pairs by filename matching.

    Patterns: test_X.py <-> X.py, X.test.js <-> X.js, X_test.go <-> X.go
    Returns {source_symbol_id: [{testId, testPath, matchType}]}.
    """
    # Group test files by stem
    test_stems: dict[str, list[str]] = defaultdict(list)

    for fpath in index.files:
        if not _is_test_path(fpath):
            continue
        basename = fpath.rsplit("/", 1)[-1]
        stem = _extract_test_stem(basename)
        if stem:
            test_stems[stem.lower()].append(fpath)

    if not test_stems:
        return {}

    # Build source file stem lookup
    source_stems: dict[str, list[str]] = defaultdict(list)
    for fpath in index.files:
        if _is_test_path(fpath):
            continue
        basename = fpath.rsplit("/", 1)[-1]
        stem = basename.rsplit(".", 1)[0] if "." in basename else basename
        source_stems[stem.lower()].append(fpath)

    # Match and tag symbols
    result: dict[str, list[dict]] = {}

    for stem, test_paths in test_stems.items():
        if stem not in source_stems:
            continue

        for src_path in source_stems[stem]:
            for sym_id, sym in index.symbols.items():
                if sym.path != src_path:
                    continue
                if sym.symbol_type not in SHOULD_BE_REFERENCED:
                    continue
                if (sym.end_line - sym.start_line) < MIN_LINES:
                    continue

                test_refs = []
                for tp in test_paths:
                    test_basename = tp.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    test_refs.append({
                        "testId": f"{sym.project}:{tp}:file:{test_basename}",
                        "testPath": tp,
                        "matchType": "filename",
                    })

                if test_refs:
                    result[sym_id] = test_refs

    return result


def _extract_test_stem(basename: str) -> str | None:
    """Extract source file stem from test filename.

    test_auth.py -> auth
    auth.test.js -> auth
    auth_test.go -> auth
    auth.spec.ts -> auth
    """
    name = basename.rsplit(".", 1)[0] if "." in basename else basename

    # test_X
    if name.startswith("test_"):
        return name[5:] if len(name) > 5 else None

    # X.test or X.spec
    for suffix in [".test", ".spec"]:
        idx = name.rfind(suffix)
        if idx > 0:
            return name[:idx]

    # X_test or X_spec (must be at end)
    if name.endswith("_test"):
        return name[:-5]
    if name.endswith("_spec"):
        return name[:-5]

    return None


def _is_test_path(path: str) -> bool:
    """Check if a file path belongs to a test file."""
    p = path.lower()
    for indicator in TEST_PATH_INDICATORS:
        if indicator in p:
            return True
    basename = p.rsplit("/", 1)[-1]
    for pattern in TEST_NAME_PATTERNS:
        if pattern in basename:
            return True
    return False
