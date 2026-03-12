"""Fuzzing / property-based tests for incremental indexing correctness.

Verifies that incremental updates produce the same results as full rebuilds
for: reverse_index, BM25 search, dependency resolution, and end-to-end scans.

Uses hypothesis for property-based test generation.
"""

import os
import random
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from bm25 import BM25Index, tokenize
from models import Dependency, DependencyType, Symbol, SymbolType


# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Generate valid Python-ish identifier names
_ident_chars = st.sampled_from("abcdefghijklmnopqrstuvwxyz")
_ident_strategy = st.text(_ident_chars, min_size=3, max_size=12).filter(
    lambda s: s.isidentifier()
)

# File path segments
_file_name_strategy = st.builds(
    lambda name: f"{name}.py",
    _ident_strategy,
)

_file_path_strategy = st.builds(
    lambda pkg, name: f"{pkg}/{name}.py",
    _ident_strategy,
    _ident_strategy,
)

# A short text blob for BM25 documents
_doc_text_strategy = st.lists(
    _ident_strategy, min_size=2, max_size=10
).map(lambda words: " ".join(words))

# Symbol types that are commonly used
_sym_types = st.sampled_from([
    SymbolType.FUNCTION, SymbolType.CLASS, SymbolType.METHOD,
])

# Dependency types that are tracked by _build_reverse_index
_dep_types = st.sampled_from([
    DependencyType.CALLS, DependencyType.EXTENDS,
    DependencyType.IMPLEMENTS, DependencyType.USES,
])


def _make_symbol(project: str, path: str, sym_type: SymbolType, name: str) -> Symbol:
    """Create a Symbol with reasonable defaults."""
    return Symbol(
        project=project,
        path=path,
        symbol_type=sym_type,
        name=name,
        start_line=1,
        end_line=10,
        content=f"def {name}(): pass",
        language="python",
    )


# =============================================================================
# 1. Reverse Index Consistency
# =============================================================================


class TestReverseIndexFuzz:
    """Property: incremental _build_reverse_index == full rebuild."""

    @given(
        file_paths=st.lists(
            _file_path_strategy, min_size=3, max_size=8, unique=True
        ),
        sym_names=st.lists(
            _ident_strategy, min_size=6, max_size=20, unique=True
        ),
        seed=st.integers(min_value=0, max_value=2**31),
    )
    @settings(
        max_examples=30,
        deadline=10000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_incremental_matches_full_rebuild(self, file_paths, sym_names, seed):
        """Generate random symbol graph, apply incremental update, compare."""
        from src.engine import IndexEngine

        rng = random.Random(seed)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            idx_dir = root / ".flyto-index"
            project = "fuzz"

            # Assign symbol names to files
            name_iter = iter(sym_names)
            file_sym_map = {}  # path -> [name, ...]
            for fp in file_paths:
                count = rng.randint(1, max(1, len(sym_names) // len(file_paths)))
                names = []
                for _ in range(count):
                    try:
                        names.append(next(name_iter))
                    except StopIteration:
                        break
                if names:
                    file_sym_map[fp] = names

            if not file_sym_map:
                return  # degenerate case

            # Write Python files
            for fp, names in file_sym_map.items():
                full_path = root / fp
                full_path.parent.mkdir(parents=True, exist_ok=True)
                lines = []
                for n in names:
                    lines.append(f"def {n}():\n    pass\n")
                full_path.write_text("\n".join(lines))

            # Full scan (baseline)
            engine = IndexEngine(project, root, index_dir=idx_dir)
            engine.scan(incremental=False)

            # Pick random files to "change" (modify content)
            changed_files = rng.sample(
                list(file_sym_map.keys()),
                k=min(rng.randint(1, 3), len(file_sym_map)),
            )
            for fp in changed_files:
                full_path = root / fp
                # Re-write with slightly different content
                old_names = file_sym_map[fp]
                new_names = []
                for n in old_names:
                    if rng.random() < 0.5:
                        new_names.append(n)  # keep
                    # else: drop (simulates symbol removal)
                # Maybe add a new symbol
                if rng.random() < 0.5:
                    new_name = f"new_{rng.randint(0, 999)}"
                    new_names.append(new_name)
                if not new_names:
                    new_names.append("fallback_sym")
                lines = []
                for n in new_names:
                    lines.append(f"def {n}():\n    pass\n")
                full_path.write_text("\n".join(lines))

            # Incremental scan
            engine_incr = IndexEngine(project, root, index_dir=idx_dir)
            engine_incr.scan(incremental=True)
            incr_reverse = dict(engine_incr.index.reverse_index)

            # Full rebuild for comparison
            engine_full = IndexEngine(project, root, index_dir=idx_dir)
            engine_full.scan(incremental=False)
            full_reverse = dict(engine_full.index.reverse_index)

            # Compare: sort caller lists for stable comparison
            for k in set(list(incr_reverse.keys()) + list(full_reverse.keys())):
                incr_callers = sorted(incr_reverse.get(k, []))
                full_callers = sorted(full_reverse.get(k, []))
                assert incr_callers == full_callers, (
                    f"Reverse index mismatch for {k}:\n"
                    f"  incremental: {incr_callers}\n"
                    f"  full:        {full_callers}"
                )


# =============================================================================
# 2. BM25 Incremental Consistency
# =============================================================================


class TestBM25Fuzz:
    """Property: BM25 incremental update_docs produces same search results as full rebuild."""

    @given(
        initial_docs=st.dictionaries(
            keys=st.text("abcdefghijklmnop", min_size=3, max_size=8),
            values=_doc_text_strategy,
            min_size=4,
            max_size=15,
        ),
        remove_fraction=st.floats(min_value=0.0, max_value=0.6),
        new_docs=st.dictionaries(
            keys=st.text("qrstuvwxyz", min_size=3, max_size=8),
            values=_doc_text_strategy,
            min_size=0,
            max_size=5,
        ),
        query=_doc_text_strategy,
    )
    @settings(
        max_examples=50,
        deadline=10000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_incremental_vs_full_rebuild(self, initial_docs, remove_fraction, new_docs, query):
        """Incremental update_docs must match a fresh build from the final doc set."""
        if not initial_docs:
            return

        # Decide which docs to remove
        rng = random.Random(42)
        remove_count = int(len(initial_docs) * remove_fraction)
        remove_ids = set(rng.sample(list(initial_docs.keys()), k=remove_count))

        # Final expected document set
        final_docs = {k: v for k, v in initial_docs.items() if k not in remove_ids}
        final_docs.update(new_docs)

        if not final_docs:
            return

        # Method 1: Full rebuild from final docs
        full_idx = BM25Index()
        full_idx.build(final_docs)

        # Method 2: Incremental — build initial, then update
        incr_idx = BM25Index()
        incr_idx.build(initial_docs)
        incr_idx.update_docs(removed_ids=remove_ids, added_docs=new_docs)

        # Structural checks
        assert incr_idx.N == full_idx.N, (
            f"Doc count mismatch: incremental={incr_idx.N}, full={full_idx.N}"
        )
        assert sorted(incr_idx.doc_ids) == sorted(full_idx.doc_ids)
        assert abs(incr_idx.avgdl - full_idx.avgdl) < 1e-9

        # df must match
        assert incr_idx.df == full_idx.df, (
            f"df mismatch:\n  incr: {incr_idx.df}\n  full: {full_idx.df}"
        )

        # Search results must match
        query_tokens = tokenize(query)
        if not query_tokens:
            return

        full_results = dict(full_idx.search(query, top_k=20))
        incr_results = dict(incr_idx.search(query, top_k=20))

        assert set(full_results.keys()) == set(incr_results.keys()), (
            f"Result key mismatch for query '{query}':\n"
            f"  full:  {sorted(full_results.keys())}\n"
            f"  incr:  {sorted(incr_results.keys())}"
        )
        for doc_id in full_results:
            assert abs(full_results[doc_id] - incr_results[doc_id]) < 1e-6, (
                f"Score mismatch for '{doc_id}' on query '{query}': "
                f"full={full_results[doc_id]}, incr={incr_results[doc_id]}"
            )


# =============================================================================
# 3. Dependency Resolution Consistency
# =============================================================================


class TestDependencyResolutionFuzz:
    """Property: incremental dependency resolution matches full resolution."""

    @given(
        num_files=st.integers(min_value=3, max_value=6),
        seed=st.integers(min_value=0, max_value=2**31),
    )
    @settings(
        max_examples=20,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_incremental_deps_match_full(self, num_files, seed):
        """Create files with cross-references, change some, compare resolution."""
        from src.engine import IndexEngine

        rng = random.Random(seed)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            idx_dir = root / ".flyto-index"

            # Generate files with functions that call each other
            file_names = [f"mod_{i}.py" for i in range(num_files)]
            func_names = [f"func_{i}" for i in range(num_files)]

            for i, fname in enumerate(file_names):
                lines = []
                # Import from another file
                other = rng.choice([j for j in range(num_files) if j != i])
                lines.append(f"from mod_{other} import func_{other}")
                lines.append("")
                lines.append(f"def {func_names[i]}():")
                lines.append(f"    func_{other}()")
                lines.append("")
                (root / fname).write_text("\n".join(lines))

            # Full scan
            engine = IndexEngine("fuzz", root, index_dir=idx_dir)
            engine.scan(incremental=False)

            # Modify a random file
            change_idx = rng.randint(0, num_files - 1)
            other = rng.choice([j for j in range(num_files) if j != change_idx])
            new_lines = [
                f"from mod_{other} import func_{other}",
                "",
                f"def {func_names[change_idx]}():",
                f"    func_{other}()",
                f"    return 42",
                "",
            ]
            (root / file_names[change_idx]).write_text("\n".join(new_lines))

            # Incremental scan
            engine_incr = IndexEngine("fuzz", root, index_dir=idx_dir)
            engine_incr.scan(incremental=True)

            # Full rebuild
            engine_full = IndexEngine("fuzz", root, index_dir=idx_dir)
            engine_full.scan(incremental=False)

            # Compare dependencies (by id set)
            incr_dep_ids = set(engine_incr.index.dependencies.keys())
            full_dep_ids = set(engine_full.index.dependencies.keys())
            assert incr_dep_ids == full_dep_ids, (
                f"Dependency ID mismatch:\n"
                f"  only in incr: {incr_dep_ids - full_dep_ids}\n"
                f"  only in full: {full_dep_ids - incr_dep_ids}"
            )

            # Compare resolved targets
            for dep_id in full_dep_ids:
                incr_dep = engine_incr.index.dependencies[dep_id]
                full_dep = engine_full.index.dependencies[dep_id]
                incr_resolved = incr_dep.metadata.get("resolved_target", "")
                full_resolved = full_dep.metadata.get("resolved_target", "")
                assert incr_resolved == full_resolved, (
                    f"Resolved target mismatch for {dep_id}: "
                    f"incr={incr_resolved}, full={full_resolved}"
                )


# =============================================================================
# 4. End-to-End: Full vs Incremental Scan
# =============================================================================


class TestEndToEndFuzz:
    """End-to-end test: full scan after incremental must match a fresh full scan."""

    @given(seed=st.integers(min_value=0, max_value=2**31))
    @settings(
        max_examples=10,
        deadline=20000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_full_vs_incremental_scan(self, seed):
        """Create project, full scan, modify, incremental scan, compare with fresh full."""
        from src.engine import IndexEngine

        rng = random.Random(seed)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Create initial project files
            files = {
                "utils.py": (
                    "def helper():\n"
                    "    \"\"\"A helper function.\"\"\"\n"
                    "    return 1\n"
                    "\n"
                    "def another_helper():\n"
                    "    return helper()\n"
                ),
                "main.py": (
                    "from utils import helper, another_helper\n"
                    "\n"
                    "def main():\n"
                    "    helper()\n"
                    "    another_helper()\n"
                ),
                "service.py": (
                    "from utils import helper\n"
                    "\n"
                    "class Service:\n"
                    "    def run(self):\n"
                    "        helper()\n"
                ),
            }
            for fname, content in files.items():
                (root / fname).write_text(content)

            # Full scan (baseline)
            idx_dir = root / ".flyto-index"
            engine = IndexEngine("e2e", root, index_dir=idx_dir)
            engine.scan(incremental=False)

            # Apply random modifications
            modifications = rng.sample(
                ["modify_utils", "modify_main", "add_file", "modify_service"],
                k=rng.randint(1, 3),
            )

            for mod in modifications:
                if mod == "modify_utils":
                    (root / "utils.py").write_text(
                        "def helper():\n"
                        "    \"\"\"Modified helper.\"\"\"\n"
                        "    return 42\n"
                        "\n"
                        "def another_helper():\n"
                        "    return helper() + 1\n"
                        "\n"
                        "def brand_new():\n"
                        "    return 'new'\n"
                    )
                elif mod == "modify_main":
                    (root / "main.py").write_text(
                        "from utils import helper\n"
                        "\n"
                        "def main():\n"
                        "    helper()\n"
                    )
                elif mod == "add_file":
                    (root / "extra.py").write_text(
                        "from utils import helper\n"
                        "\n"
                        "def extra_func():\n"
                        "    helper()\n"
                        "    return True\n"
                    )
                elif mod == "modify_service":
                    (root / "service.py").write_text(
                        "class Service:\n"
                        "    def run(self):\n"
                        "        pass  # no longer uses helper\n"
                    )

            # Incremental scan
            engine_incr = IndexEngine("e2e", root, index_dir=idx_dir)
            engine_incr.scan(incremental=True)

            # Fresh full scan for comparison
            idx_dir_full = root / ".flyto-index-full"
            engine_full = IndexEngine("e2e", root, index_dir=idx_dir_full)
            engine_full.scan(incremental=False)

            # Compare symbols
            incr_sym_ids = set(engine_incr.index.symbols.keys())
            full_sym_ids = set(engine_full.index.symbols.keys())
            assert incr_sym_ids == full_sym_ids, (
                f"Symbol ID mismatch:\n"
                f"  only in incr: {incr_sym_ids - full_sym_ids}\n"
                f"  only in full: {full_sym_ids - incr_sym_ids}"
            )

            # Compare reverse_index
            for k in set(
                list(engine_incr.index.reverse_index.keys())
                + list(engine_full.index.reverse_index.keys())
            ):
                incr_callers = sorted(engine_incr.index.reverse_index.get(k, []))
                full_callers = sorted(engine_full.index.reverse_index.get(k, []))
                assert incr_callers == full_callers, (
                    f"Reverse index mismatch for {k}:\n"
                    f"  incremental: {incr_callers}\n"
                    f"  full:        {full_callers}"
                )

            # Compare dependencies
            incr_dep_ids = set(engine_incr.index.dependencies.keys())
            full_dep_ids = set(engine_full.index.dependencies.keys())
            assert incr_dep_ids == full_dep_ids, (
                f"Dependency mismatch:\n"
                f"  only in incr: {incr_dep_ids - full_dep_ids}\n"
                f"  only in full: {full_dep_ids - incr_dep_ids}"
            )

            # Compare reference counts on symbols
            for sid in full_sym_ids:
                incr_ref = engine_incr.index.symbols[sid].reference_count
                full_ref = engine_full.index.symbols[sid].reference_count
                assert incr_ref == full_ref, (
                    f"Ref count mismatch for {sid}: "
                    f"incr={incr_ref}, full={full_ref}"
                )


# =============================================================================
# 5. BM25 update_docs idempotency (edge-case fuzz)
# =============================================================================


class TestBM25EdgeCases:
    """Additional BM25 edge-case and idempotency tests."""

    @given(
        docs=st.dictionaries(
            keys=st.text("abcdef", min_size=2, max_size=6),
            values=_doc_text_strategy,
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=30, deadline=5000)
    def test_remove_then_readd_is_identity(self, docs):
        """Removing all docs then re-adding them should produce identical index."""
        if not docs:
            return

        idx_original = BM25Index()
        idx_original.build(docs)

        idx_roundtrip = BM25Index()
        idx_roundtrip.build(docs)
        idx_roundtrip.update_docs(removed_ids=set(docs.keys()), added_docs=docs)

        assert idx_roundtrip.N == idx_original.N
        assert sorted(idx_roundtrip.doc_ids) == sorted(idx_original.doc_ids)
        assert idx_roundtrip.df == idx_original.df
        assert abs(idx_roundtrip.avgdl - idx_original.avgdl) < 1e-9

    @given(
        docs=st.dictionaries(
            keys=st.text("abcdef", min_size=2, max_size=6),
            values=_doc_text_strategy,
            min_size=2,
            max_size=8,
        ),
        query=_doc_text_strategy,
    )
    @settings(max_examples=30, deadline=5000)
    def test_double_noop_update_stable(self, docs, query):
        """Two consecutive noop updates should not change search results."""
        if not docs:
            return

        idx = BM25Index()
        idx.build(docs)
        results_before = idx.search(query, top_k=20)

        idx.update_docs(removed_ids=set(), added_docs={})
        idx.update_docs(removed_ids=set(), added_docs={})
        results_after = idx.search(query, top_k=20)

        assert results_before == results_after
