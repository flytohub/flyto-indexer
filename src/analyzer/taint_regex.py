"""Regex fallback analysis for non-Python source languages."""

from __future__ import annotations

import re

from .taint_common import MAX_FINDINGS, SKIP_DIR_PATTERNS, _in_hidden_dir, _is_generated_asset
from .taint_evidence import TaintFlow
from .taint_rules import GO_TAINT_PATTERNS, JS_TAINT_PATTERNS


class TaintRegexMixin:
    def _scan_regex_languages(self):
        """Scan non-Python files with targeted regex patterns."""
        ext_map = {
            ".js": JS_TAINT_PATTERNS,
            ".jsx": JS_TAINT_PATTERNS,
            ".ts": JS_TAINT_PATTERNS,
            ".tsx": JS_TAINT_PATTERNS,
            ".go": GO_TAINT_PATTERNS,
        }

        for ext, patterns in ext_map.items():
            if len(self.findings) >= MAX_FINDINGS:
                return
            for fpath in self._filesystem_paths(f"*{ext}"):
                if len(self.findings) >= MAX_FINDINGS:
                    return
                rel = str(fpath.relative_to(self.project_root)).replace("\\", "/")
                if SKIP_DIR_PATTERNS.search(rel) or _in_hidden_dir(rel):
                    continue
                # Minified and vendored bundles are not this project's code.
                # gogs ships jquery.min.js and mermaid.min.js; a single line of
                # a minified bundle is tens of thousands of characters, so a
                # line-oriented regex matches something in nearly all of them.
                # Both of gogs's only two "findings" were exactly this.
                if _is_generated_asset(rel):
                    continue

                content = self._read_taint_source(rel)
                if content is None:
                    continue

                # Count sources/sinks for non-Python
                lang = "javascript" if ext in (".js", ".jsx", ".ts", ".tsx") else "go"
                self._count_sources_sinks(content, lang)

                self._scan_file_regex(rel, content, patterns)

    def _scan_file_regex(
        self,
        file_path: str,
        content: str,
        patterns: list[tuple[str, str, str, str]],
    ):
        """Scan a file's lines with regex taint patterns."""
        lines = content.split("\n")
        # For multi-line patterns, also scan consecutive line pairs
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("#"):
                continue

            # Check single line
            for pat, vuln_type, severity, rec in patterns:
                if re.search(pat, line, re.IGNORECASE):
                    self.findings.append(TaintFlow(
                        file_path=file_path,
                        line=i + 1,
                        severity=severity,
                        category=vuln_type,
                        source_expr="(regex match)",
                        sink_expr=line.strip()[:120],
                        flow_chain=[line.strip()[:120]],
                        recommendation=rec,
                        source_file=file_path,
                        source_line=i + 1,
                        sink_file=file_path,
                        sink_line=i + 1,
                        path=[f"{file_path}:{i + 1}"],
                        sanitized=False, callee_resolution="regex_candidate",
                    ))
                    break

            # Check two-line window for flows split across lines
            if i + 1 < len(lines):
                two_lines = line + " " + lines[i + 1]
                for pat, vuln_type, severity, rec in patterns:
                    if re.search(pat, two_lines, re.IGNORECASE):
                        # Only emit a window finding when the rule genuinely
                        # spans both lines. The next iteration owns matches
                        # wholly contained in the second line.
                        if (
                            not re.search(pat, line, re.IGNORECASE)
                            and not re.search(pat, lines[i + 1], re.IGNORECASE)
                        ):
                            self.findings.append(TaintFlow(
                                file_path=file_path,
                                line=i + 1,
                                severity=severity,
                                category=vuln_type,
                                source_expr="(regex match)",
                                sink_expr=two_lines.strip()[:120],
                                flow_chain=[two_lines.strip()[:120]],
                                recommendation=rec,
                                source_file=file_path,
                                source_line=i + 1,
                                sink_file=file_path,
                                sink_line=i + 1,
                                path=[f"{file_path}:{i + 1}"],
                                sanitized=False, callee_resolution="regex_candidate",
                            ))
                        break

