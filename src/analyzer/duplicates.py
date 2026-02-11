"""
Duplicate code detection - find copy-pasted code

Strategy:
1. Split code into chunks (N consecutive lines)
2. Normalize (remove whitespace, comments)
3. Compute hash, find duplicates
4. Merge adjacent duplicate blocks
"""

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DuplicateBlock:
    """Duplicate code block"""
    file1: str
    start1: int
    end1: int
    file2: str
    start2: int
    end2: int
    lines: int
    similarity: float
    code_preview: str = ""


@dataclass
class DuplicateReport:
    """Duplicate code report"""
    total_files: int = 0
    total_lines: int = 0
    duplicate_blocks: list[DuplicateBlock] = field(default_factory=list)
    duplicate_lines: int = 0

    @property
    def duplicate_rate(self) -> float:
        if self.total_lines == 0:
            return 0
        return self.duplicate_lines / self.total_lines * 100


class DuplicateDetector:
    """Duplicate code detector"""

    def __init__(
        self,
        project_root: Path,
        min_lines: int = 6,  # Minimum duplicate line count
        extensions: list[str] = None,
        ignore_patterns: list[str] = None,
    ):
        self.project_root = project_root
        self.min_lines = min_lines
        self.extensions = extensions or [".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".java", ".go"]
        self.ignore_patterns = ignore_patterns or [
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".nuxt", ".output",
            "test", "tests", "__tests__",
        ]

        # chunk hash -> [(file, start_line, lines)]
        self.chunk_index: dict[str, list[tuple[str, int, list[str]]]] = defaultdict(list)

    def _should_skip(self, path: str) -> bool:
        return any(pattern in path for pattern in self.ignore_patterns)

    def _normalize_line(self, line: str) -> str:
        """Normalize code line (remove whitespace and comments)"""
        line = line.strip()

        # Remove single-line comments
        if line.startswith("#") or line.startswith("//"):
            return ""

        # Remove trailing comments
        for comment_start in ["#", "//"]:
            if comment_start in line:
                # Simple handling, ignoring # inside strings
                idx = line.find(comment_start)
                if idx > 0:
                    line = line[:idx].strip()

        # Remove extra whitespace
        line = re.sub(r'\s+', ' ', line)

        return line

    def _extract_chunks(self, rel_path: str, content: str) -> list[tuple[int, str, list[str]]]:
        """Extract code chunks"""
        lines = content.split("\n")
        chunks = []

        # Normalize all lines
        normalized = []
        for i, line in enumerate(lines):
            norm = self._normalize_line(line)
            if norm:  # Only keep lines with content
                normalized.append((i + 1, norm, line))

        # Sliding window extract chunks
        for i in range(len(normalized) - self.min_lines + 1):
            chunk_lines = normalized[i:i + self.min_lines]
            start_line = chunk_lines[0][0]

            # Compute hash
            chunk_text = "\n".join(line[1] for line in chunk_lines)
            chunk_hash = hashlib.md5(chunk_text.encode()).hexdigest()

            # Original source code
            original_lines = [line[2] for line in chunk_lines]

            chunks.append((start_line, chunk_hash, original_lines))

        return chunks

    def scan_directory(self) -> list[str]:
        """Scan directory"""
        files = []
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if not self._should_skip(rel_path):
                    files.append(rel_path)
        return files

    def analyze(self) -> DuplicateReport:
        """Run analysis"""
        report = DuplicateReport()

        files = self.scan_directory()
        report.total_files = len(files)

        # First pass: build chunk index
        for rel_path in files:
            full_path = self.project_root / rel_path
            try:
                content = full_path.read_text(encoding="utf-8")
                report.total_lines += len(content.split("\n"))
            except Exception:
                continue

            chunks = self._extract_chunks(rel_path, content)
            for start_line, chunk_hash, original_lines in chunks:
                self.chunk_index[chunk_hash].append((rel_path, start_line, original_lines))

        # Second pass: find duplicates
        seen_pairs = set()
        duplicates_raw = []

        for _chunk_hash, locations in self.chunk_index.items():
            if len(locations) < 2:
                continue

            # Find all pairs
            for i, (file1, start1, lines1) in enumerate(locations):
                for file2, start2, _lines2 in locations[i + 1:]:
                    # Skip adjacent duplicates within the same file
                    if file1 == file2 and abs(start1 - start2) < self.min_lines:
                        continue

                    # Deduplicate
                    pair_key = tuple(sorted([(file1, start1), (file2, start2)]))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    duplicates_raw.append({
                        "file1": file1,
                        "start1": start1,
                        "end1": start1 + self.min_lines - 1,
                        "file2": file2,
                        "start2": start2,
                        "end2": start2 + self.min_lines - 1,
                        "lines": lines1,
                    })

        # Merge adjacent duplicate blocks
        merged = self._merge_adjacent(duplicates_raw)

        for dup in merged:
            block = DuplicateBlock(
                file1=dup["file1"],
                start1=dup["start1"],
                end1=dup["end1"],
                file2=dup["file2"],
                start2=dup["start2"],
                end2=dup["end2"],
                lines=dup["end1"] - dup["start1"] + 1,
                similarity=1.0,
                code_preview="\n".join(dup["lines"][:5]),
            )
            report.duplicate_blocks.append(block)
            report.duplicate_lines += block.lines

        # Sort by line count
        report.duplicate_blocks.sort(key=lambda x: x.lines, reverse=True)

        return report

    def _merge_adjacent(self, duplicates: list[dict]) -> list[dict]:
        """Merge adjacent duplicate blocks"""
        if not duplicates:
            return []

        # Sort by file and start line
        duplicates.sort(key=lambda x: (x["file1"], x["file2"], x["start1"], x["start2"]))

        merged = []
        current = None

        for dup in duplicates:
            if current is None:
                current = dup.copy()
                continue

            # Check if adjacent
            same_files = (current["file1"] == dup["file1"] and current["file2"] == dup["file2"])
            adjacent1 = abs(dup["start1"] - current["end1"]) <= 2
            adjacent2 = abs(dup["start2"] - current["end2"]) <= 2

            if same_files and adjacent1 and adjacent2:
                # Merge
                current["end1"] = max(current["end1"], dup["end1"])
                current["end2"] = max(current["end2"], dup["end2"])
                current["lines"] = current.get("lines", []) + dup.get("lines", [])
            else:
                merged.append(current)
                current = dup.copy()

        if current:
            merged.append(current)

        return merged

    def print_report(self, report: DuplicateReport):
        """Print the report"""
        print(f"\n{'='*70}")
        print("Duplicate Code Analysis")
        print(f"{'='*70}")
        print(f"\nFiles scanned: {report.total_files}")
        print(f"Total lines: {report.total_lines}")
        print(f"Duplicate blocks: {len(report.duplicate_blocks)}")
        print(f"Duplicate lines: {report.duplicate_lines} ({report.duplicate_rate:.1f}%)")

        if report.duplicate_blocks:
            print(f"\n{'='*70}")
            print("DUPLICATE CODE BLOCKS (top 15)")
            print(f"{'='*70}")

            for block in report.duplicate_blocks[:15]:
                print(f"\n  {block.file1}:{block.start1}-{block.end1}")
                print(f"  ≈ {block.file2}:{block.start2}-{block.end2}")
                print(f"  Lines: {block.lines}")
                if block.code_preview:
                    preview_lines = block.code_preview.split("\n")[:3]
                    for line in preview_lines:
                        print(f"    | {line[:60]}")
                    if len(block.code_preview.split("\n")) > 3:
                        print("    | ...")
        else:
            print("\n  No significant duplicates found")


def detect_duplicates(project_path: Path, min_lines: int = 6) -> DuplicateReport:
    """Convenience function"""
    detector = DuplicateDetector(project_path, min_lines)
    return detector.analyze()
