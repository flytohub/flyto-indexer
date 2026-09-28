"""Deterministic semantic-preserving mutation cases for the accuracy gate.

The canonical corpus provides human-readable ground truth.  This module expands
those cases with auditable mutations that should not change the expected
security category: comment decoys, identifier renames, receiver aliases, and
layout/safe-neighbour noise.  It intentionally does not invent new expected
findings from analyzer output.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MUTATION_DIMENSIONS = (
    "comment_decoys",
    "identifier_rename",
    "receiver_alias",
    "layout_safe_neighbor",
)

_COMMENT_DECOYS = {
    "python": (
        "# accuracy decoy: request.args.get('id'); cursor.execute('SELECT ' + id)\n"
        "# accuracy decoy: os.system(request.args.get('cmd'))\n"
    ),
    "javascript": (
        "// accuracy decoy: axios.get(req.query.url)\n"
        "// accuracy decoy: db.query('SELECT ' + req.query.id)\n"
    ),
    "typescript": (
        "// accuracy decoy: axios.get(req.query.url)\n"
        "// accuracy decoy: db.query('SELECT ' + req.query.id)\n"
    ),
    "go": (
        "// accuracy decoy: http.Get(r.FormValue(\"url\"))\n"
        "// accuracy decoy: db.Query(\"SELECT \" + r.FormValue(\"id\"))\n"
    ),
}

_RENAMES = {
    "python": {
        "user_id": "external_id",
        "command": "action_text",
        "template": "page_template",
        "document": "xml_payload",
        "fragment": "html_fragment",
        "filename": "resource_name",
        "pattern": "search_pattern",
        "marker": "needle",
        "query": "lookup_value",
    },
    "javascript": {
        "upstream": "remote_response",
        "settings": "merged_settings",
        "rows": "result_rows",
        "pattern": "search_pattern",
        "defaults": "default_settings",
    },
    "typescript": {
        "upstream": "remote_response",
        "settings": "merged_settings",
        "rows": "result_rows",
        "pattern": "search_pattern",
        "defaults": "default_settings",
    },
    "go": {
        "command": "commandText",
        "slugPattern": "slugMatcher",
    },
}


def _replace_words(text: str, replacements: dict[str, str]) -> str:
    for source, target in replacements.items():
        text = re.sub(rf"\b{re.escape(source)}\b", target, text)
    return text


def _case_files(corpus_root: Path, case: dict[str, Any]) -> dict[str, str]:
    project = corpus_root / str(case["path"])
    files: dict[str, str] = {}
    for path in sorted(project.rglob("*")):
        if path.is_file():
            files[path.relative_to(project).as_posix()] = path.read_text(encoding="utf-8")
    if not files:
        raise ValueError(f"Mutation base case has no files: {case['id']}")
    return files


def _comment_decoys(files: dict[str, str], language: str) -> dict[str, str]:
    prefix = _COMMENT_DECOYS[language]
    return {path: prefix + content for path, content in files.items()}


def _identifier_rename(files: dict[str, str], language: str) -> dict[str, str]:
    replacements = _RENAMES[language]
    marker = _COMMENT_DECOYS[language].splitlines(keepends=True)[0]
    return {
        path: marker + _replace_words(content, replacements)
        for path, content in files.items()
    }


def _receiver_alias(files: dict[str, str], language: str) -> dict[str, str]:
    transformed = dict(files)
    if language == "python":
        transformed = {
            path: _replace_words(
                content,
                {
                    "cursor": "connection",
                    "db": "database",
                },
            )
            for path, content in files.items()
        }
    elif language in {"javascript", "typescript"}:
        transformed = {
            path: _replace_words(
                content,
                {
                    "res": "response",
                    "db": "database",
                },
            )
            for path, content in files.items()
        }
    elif language == "go":
        transformed = {
            path: re.sub(r"\bdb\b", "conn", content)
            for path, content in files.items()
        }
    marker = _COMMENT_DECOYS[language].splitlines(keepends=True)[0]
    return {path: marker + content for path, content in transformed.items()}


def _safe_neighbor(language: str) -> tuple[str, str]:
    if language == "python":
        return (
            "safe_neighbor.py",
            "def health():\n    return 'ok'\n\n# request.args cursor.execute os.system\n",
        )
    if language == "javascript":
        return (
            "safe-neighbor.js",
            "function health() { return 'ok' }\n// req.query axios.get db.query\n",
        )
    if language == "typescript":
        return (
            "safe-neighbor.ts",
            "function health(): string { return 'ok' }\n// req.query axios.get db.query\n",
        )
    return (
        "safe_neighbor.go",
        'package main\n\nfunc health() string { return "ok" }\n// r.FormValue http.Get db.Query\n',
    )


def _layout_safe_neighbor(files: dict[str, str], language: str) -> dict[str, str]:
    transformed: dict[str, str] = {}
    for path, content in files.items():
        if language in {"javascript", "typescript"}:
            content = content.replace("; ", ";\n  ", 1)
        elif language == "python":
            content = "\n" + content.replace("\n\n", "\n\n\n", 1)
        else:
            content = content.replace("\n\n", "\n\n\n", 1)
        transformed[path] = content
    neighbor_path, neighbor_source = _safe_neighbor(language)
    transformed[neighbor_path] = neighbor_source
    return transformed


_TRANSFORMS = {
    "comment_decoys": _comment_decoys,
    "identifier_rename": _identifier_rename,
    "receiver_alias": _receiver_alias,
    "layout_safe_neighbor": _layout_safe_neighbor,
}


def _safe_dimension_marker(language: str, dimension: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_]", "_", dimension)
    if language == "python":
        return f"\n_accuracy_{token} = 1\n"
    if language in {"javascript", "typescript"}:
        return f"\nconst __accuracy_{token} = 1;\n"
    return f"\nvar accuracy_{token} = 1\n"


def _mark_variant(
    files: dict[str, str],
    language: str,
    dimension: str,
) -> dict[str, str]:
    """Make every variant structurally unique with a harmless program symbol."""
    marked = dict(files)
    first_path = sorted(marked)[0]
    marked[first_path] = marked[first_path].rstrip() + _safe_dimension_marker(
        language, dimension
    )
    return marked


def build_mutation_cases(
    corpus_root: str | Path,
    canonical_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build deterministic mutation cases from canonical, human-labelled cases."""
    root = Path(corpus_root).resolve()
    mutations: list[dict[str, Any]] = []
    for base in canonical_cases:
        language = str(base.get("language") or "")
        if language not in _TRANSFORMS and language not in _COMMENT_DECOYS:
            continue
        original = _case_files(root, base)
        for dimension in MUTATION_DIMENSIONS:
            files = _mark_variant(
                _TRANSFORMS[dimension](original, language),
                language,
                dimension,
            )
            mutations.append({
                **base,
                "id": f"{base['id']}--{dimension}",
                "path": f"@mutation/{base['id']}/{dimension}",
                "files": files,
                "corpus_kind": "mutation",
                "base_case_id": base["id"],
                "mutation_dimension": dimension,
                "metamorphic_group": None,
                "metamorphic_relation": None,
            })
    return mutations
