"""
Filesystem analysis — no index required.
"""

import os
from collections import Counter
from pathlib import Path

from .constants import SKIP_DIRS, EXT_LANG, CONFIG_FILES


_FIXTURE_DIRS = frozenset({
    "fixture", "fixtures", "testdata", "__snapshots__", "snapshots",
    "golden", "goldens", "mock", "mocks", "__mocks__",
})
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "specs"})
_EXAMPLE_DIRS = frozenset({
    "example", "examples", "sample", "samples", "demo", "demos", "playground",
})
_GENERATED_DIRS = frozenset({
    "generated", "__generated__", "gen", "autogen", "codegen",
})
_GENERATED_NAMES = frozenset({
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock",
    "cargo.lock", "composer.lock",
})
FILE_CLASSES = ("source", "test", "fixture", "example", "generated")


def classify_path(path: str) -> str:
    """Classify a project-relative path for profile scoring and reporting."""
    normalized = path.replace("\\", "/").strip("/")
    parts = [part.lower() for part in normalized.split("/") if part]
    name = parts[-1] if parts else ""
    dirs = set(parts[:-1])

    if (
        dirs & _GENERATED_DIRS
        or name in _GENERATED_NAMES
        or ".generated." in name
        or name.endswith((".min.js", ".min.css", ".pb.go", "_pb2.py"))
    ):
        return "generated"
    if dirs & _FIXTURE_DIRS:
        return "fixture"
    if dirs & _TEST_DIRS or (
        name.startswith("test_")
        or name.endswith(("_test.py", "_test.go", ".test.ts", ".test.js",
                          ".spec.ts", ".spec.js", ".spec.tsx", ".test.tsx"))
    ):
        return "test"
    if dirs & _EXAMPLE_DIRS:
        return "example"
    return "source"


def scan_filesystem(project_path: Path) -> dict:
    """Walk project directory to collect structure, languages, and signals."""
    file_count = 0
    file_class_counts = Counter()
    folder_counts = {}  # relative dir path -> file count (top 2 levels)
    lang_counter = Counter()
    config_files_found = []
    has_docker = False
    has_ci = False
    has_tests = False
    has_docs = False
    all_files = []  # relative paths for pattern detection

    # followlinks=False explicit even though it's the Python default
    # — defense in depth. flyto-indexer ships as a standalone CLI
    # (`pip install flyto-indexer`) and users point it at arbitrary
    # paths. A symlink to /etc or ~/.ssh would otherwise get scanned
    # and surface in the output. flyto-engine's scanner.go also
    # clones with core.symlinks=false; this is the indexer-side
    # mirror. Audit 2026-05-17 noted indexer had no symlink defenses.
    for dirpath, dirnames, filenames in os.walk(project_path, followlinks=False):
        # Filter skip dirs in-place + drop symlinked dirs (followlinks
        # bounds the walker but doesn't suppress them appearing in
        # dirnames; explicitly drop so they don't get scanned via
        # alternative path traversal).
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d))
        ]

        rel_dir = os.path.relpath(dirpath, project_path)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1

        for fname in filenames:
            file_count += 1
            rel_file = os.path.join(rel_dir, fname) if rel_dir != "." else fname
            all_files.append(rel_file)
            file_class_counts[classify_path(rel_file)] += 1

            # Language detection
            ext = os.path.splitext(fname)[1].lower()
            if ext in EXT_LANG:
                lang_counter[EXT_LANG[ext]] += 1

            # Folder structure (top 2 levels)
            if depth <= 2:
                if depth == 0:
                    folder_key = "."
                else:
                    parts = rel_dir.split(os.sep)
                    folder_key = os.sep.join(parts[:min(depth, 2)])
                folder_counts[folder_key] = folder_counts.get(folder_key, 0) + 1

            # Config file detection
            if fname in CONFIG_FILES:
                config_files_found.append(rel_file)

            # Infrastructure signals
            if fname.startswith("Dockerfile"):
                has_docker = True
            if fname in ("README.md", "README.rst", "README.txt", "README"):
                has_docs = True

        # Directory-level signals
        dir_name = os.path.basename(dirpath)
        if dir_name in ("docs", "doc", "documentation"):
            has_docs = True
        if dir_name in ("tests", "test", "__tests__", "spec", "specs"):
            has_tests = True

    # CI detection
    ci_paths = [
        project_path / ".github" / "workflows",
        project_path / ".gitlab-ci.yml",
        project_path / ".circleci",
        project_path / "Jenkinsfile",
        project_path / ".travis.yml",
        project_path / "bitbucket-pipelines.yml",
    ]
    for cp in ci_paths:
        if cp.exists():
            has_ci = True
            break

    # Test detection fallback: check for test files in any directory
    if not has_tests:
        for f in all_files:
            base = os.path.basename(f).lower()
            if (base.startswith("test_") or base.endswith("_test.py")
                    or base.endswith(".test.ts") or base.endswith(".test.js")
                    or base.endswith(".spec.ts") or base.endswith(".spec.js")
                    or base.endswith("_test.go")):
                has_tests = True
                break

    # Build folder structure list sorted by file count
    folder_structure = [
        {"path": k, "files": v}
        for k, v in sorted(folder_counts.items(), key=lambda x: -x[1])
    ]
    classified_counts = {
        file_class: file_class_counts.get(file_class, 0)
        for file_class in FILE_CLASSES
    }
    non_production_count = file_count - classified_counts["source"]

    return {
        # ``file_count`` remains the filesystem total for backward compatibility.
        "file_count": file_count,
        "total_file_count": file_count,
        "source_file_count": classified_counts["source"],
        "non_production_file_count": non_production_count,
        "file_class_counts": classified_counts,
        "file_count_semantics": {
            "file_count": "all non-skipped filesystem files",
            "total_file_count": "all non-skipped filesystem files",
            "source_file_count": "production/source files only",
            "non_production_file_count": "test + fixture + example + generated files",
            "indexed_file_count": "unique file paths represented in the code index",
        },
        "folder_structure": folder_structure[:30],  # cap to top 30
        "languages": dict(lang_counter.most_common()),
        "has_docker": has_docker,
        "has_ci": has_ci,
        "has_tests": has_tests,
        "has_docs": has_docs,
        "config_files": sorted(config_files_found),
        "_all_files": all_files,  # internal, for pattern detection
    }
