"""Convention extraction — learn project coding patterns from the index."""

import logging
import re
from collections import Counter, defaultdict
from typing import Optional

logger = logging.getLogger("flyto-indexer.conventions")

try:
    from ..index_store import load_index
except ImportError:
    from index_store import load_index


def extract_conventions(project: Optional[str] = None) -> dict:
    """Extract coding conventions from the indexed codebase.

    Analyzes symbol names, patterns, and structure to infer:
    - Naming conventions (snake_case vs camelCase by symbol type)
    - Common patterns (decorators, base classes, return types)
    - Import preferences (top imports by frequency)
    - Error handling patterns
    - File organization patterns

    Args:
        project: Filter to specific project

    Returns:
        {naming, patterns, imports, file_organization, summary}
    """
    index = load_index()
    symbols = index.get("symbols", {})
    dependencies = index.get("dependencies", {})

    # Filter by project if specified
    if project:
        proj_lower = project.lower()
        symbols = {k: v for k, v in symbols.items() if k.lower().startswith(proj_lower)}
        dependencies = {k: v for k, v in dependencies.items()
                       if v.get("source", "").lower().startswith(proj_lower)}

    naming = _analyze_naming(symbols)
    patterns = _analyze_patterns(symbols)
    imports = _analyze_imports(dependencies)
    file_org = _analyze_file_organization(symbols)
    error_handling = _analyze_error_handling(symbols)

    return {
        "naming": naming,
        "patterns": patterns,
        "imports": imports,
        "file_organization": file_org,
        "error_handling": error_handling,
        "summary": {
            "total_symbols_analyzed": len(symbols),
            "total_dependencies_analyzed": len(dependencies),
        },
    }


def _analyze_naming(symbols: dict) -> dict:
    """Analyze naming conventions by symbol type."""
    type_style_counts = defaultdict(lambda: Counter())

    for _sid, sym in symbols.items():
        name = sym.get("name", "")
        sym_type = sym.get("type", "")
        if not name or not sym_type:
            continue

        # Skip dunder methods
        if name.startswith("__"):
            continue

        bare = name.split(".")[-1] if "." in name else name
        style = _detect_naming_style(bare)
        if style:
            type_style_counts[sym_type][style] += 1

    # Determine dominant style per type
    conventions = {}
    for sym_type, counter in type_style_counts.items():
        total = sum(counter.values())
        dominant = counter.most_common(1)[0] if counter else ("unknown", 0)
        conventions[sym_type] = {
            "dominant_style": dominant[0],
            "percentage": round(dominant[1] / total * 100, 1) if total else 0,
            "distribution": dict(counter.most_common(5)),
            "total": total,
        }

    return conventions


def _detect_naming_style(name: str) -> Optional[str]:
    """Detect the naming style of a symbol name."""
    if not name or len(name) < 2:
        return None

    # UPPER_SNAKE_CASE (constants)
    if re.match(r'^[A-Z][A-Z0-9_]+$', name):
        return "UPPER_SNAKE"

    # PascalCase
    if re.match(r'^[A-Z][a-zA-Z0-9]+$', name) and not name.isupper():
        return "PascalCase"

    # camelCase
    if re.match(r'^[a-z][a-zA-Z0-9]+$', name) and any(c.isupper() for c in name):
        return "camelCase"

    # snake_case
    if re.match(r'^[a-z][a-z0-9_]+$', name) and '_' in name:
        return "snake_case"

    # kebab-case (rare in symbols, common in filenames)
    if '-' in name:
        return "kebab-case"

    # Single lowercase word
    if re.match(r'^[a-z]+$', name):
        return "lowercase"

    return None


def _analyze_patterns(symbols: dict) -> dict:
    """Analyze common code patterns."""
    # Base classes / parent types
    base_classes = Counter()
    return_types = Counter()
    param_counts = Counter()

    for _sid, sym in symbols.items():
        sym_type = sym.get("type", "")

        # Count return types
        returns = sym.get("returns", "")
        if returns and sym_type in ("function", "method"):
            # Normalize
            normalized = returns.strip().split("[")[0]  # list[X] -> list
            return_types[normalized] += 1

        # Count parameter counts
        params = sym.get("params", [])
        if sym_type in ("function", "method") and params:
            param_counts[len(params)] += 1

        # Count imports as potential base classes for classes
        if sym_type == "class":
            for imp in sym.get("imports", []):
                if imp and not imp.startswith("__"):
                    base_classes[imp] += 1

    return {
        "base_classes": dict(base_classes.most_common(10)),
        "return_types": dict(return_types.most_common(15)),
        "param_count_distribution": dict(param_counts.most_common(10)),
    }


def _analyze_imports(dependencies: dict) -> dict:
    """Analyze import patterns — what gets imported most."""
    import_targets = Counter()
    import_names = Counter()

    for _dep_id, dep in dependencies.items():
        if dep.get("type", "") != "imports":
            continue
        target = dep.get("target", "")
        if target:
            # Normalize: strip leading ./ or ../
            clean = re.sub(r'^\.+/?', '', target)
            import_targets[clean] += 1

        names = dep.get("metadata", {}).get("names", [])
        for name in names:
            if name and not name.startswith("_"):
                import_names[name] += 1

    return {
        "top_modules": dict(import_targets.most_common(20)),
        "top_names": dict(import_names.most_common(20)),
    }


def _analyze_file_organization(symbols: dict) -> dict:
    """Analyze how files are organized — symbols per file, file sizes."""
    file_stats = defaultdict(lambda: {"types": Counter(), "count": 0, "total_lines": 0})

    for _sid, sym in symbols.items():
        path = sym.get("path", "")
        if not path:
            continue
        sym_type = sym.get("type", "")
        lines = sym.get("end_line", 0) - sym.get("start_line", 0)

        file_stats[path]["types"][sym_type] += 1
        file_stats[path]["count"] += 1
        file_stats[path]["total_lines"] += lines

    # Compute averages
    if not file_stats:
        return {}

    total_files = len(file_stats)
    avg_symbols = sum(f["count"] for f in file_stats.values()) / total_files
    avg_lines = sum(f["total_lines"] for f in file_stats.values()) / total_files

    # File patterns: how many files have 1 class, how many have multiple functions, etc.
    patterns = Counter()
    for _path, stats in file_stats.items():
        types = stats["types"]
        if types.get("class", 0) == 1 and types.get("method", 0) > 0:
            patterns["single_class_with_methods"] += 1
        elif types.get("function", 0) > 0 and types.get("class", 0) == 0:
            patterns["functions_only"] += 1
        elif types.get("component", 0) > 0:
            patterns["component_file"] += 1
        elif types.get("class", 0) > 1:
            patterns["multi_class"] += 1
        else:
            patterns["other"] += 1

    return {
        "total_files": total_files,
        "avg_symbols_per_file": round(avg_symbols, 1),
        "avg_lines_per_file": round(avg_lines, 1),
        "file_patterns": dict(patterns.most_common()),
    }


def _analyze_error_handling(symbols: dict) -> dict:
    """Analyze error handling patterns from function content."""
    # Count patterns from symbol content/summary
    patterns = Counter()
    total_functions = 0

    for _sid, sym in symbols.items():
        if sym.get("type", "") not in ("function", "method"):
            continue
        total_functions += 1

        summary = (sym.get("summary", "") or "").lower()
        content_hint = (sym.get("content", "") or "")[:500].lower()
        text = summary + " " + content_hint

        if "try" in text and "except" in text:
            patterns["try_except"] += 1
        if "raise" in text:
            patterns["raise_exception"] += 1
        if "logger" in text or "logging" in text:
            patterns["uses_logging"] += 1
        if "return {" in text and "error" in text:
            patterns["error_dict_return"] += 1
        if "assert" in text:
            patterns["uses_assert"] += 1

    return {
        "total_functions": total_functions,
        "patterns": dict(patterns.most_common()),
    }
