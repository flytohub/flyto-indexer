"""
重複程式碼偵測 - 找出 copy-paste 的程式碼

策略：
1. 將程式碼切成 chunk（連續 N 行）
2. 正規化（移除空白、註解）
3. 計算 hash，找出重複
4. 合併相鄰的重複區塊
"""

import re
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class DuplicateBlock:
    """重複程式碼區塊"""
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
    """重複程式碼報告"""
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
    """重複程式碼偵測器"""

    def __init__(
        self,
        project_root: Path,
        min_lines: int = 6,  # 最少重複行數
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
        for pattern in self.ignore_patterns:
            if pattern in path:
                return True
        return False

    def _normalize_line(self, line: str) -> str:
        """正規化程式碼行（移除空白和註解）"""
        line = line.strip()

        # 移除單行註解
        if line.startswith("#") or line.startswith("//"):
            return ""

        # 移除行尾註解
        for comment_start in ["#", "//"]:
            if comment_start in line:
                # 簡單處理，不考慮字串內的 #
                idx = line.find(comment_start)
                if idx > 0:
                    line = line[:idx].strip()

        # 移除多餘空白
        line = re.sub(r'\s+', ' ', line)

        return line

    def _extract_chunks(self, rel_path: str, content: str) -> list[tuple[int, str, list[str]]]:
        """提取程式碼 chunks"""
        lines = content.split("\n")
        chunks = []

        # 正規化所有行
        normalized = []
        for i, line in enumerate(lines):
            norm = self._normalize_line(line)
            if norm:  # 只保留有內容的行
                normalized.append((i + 1, norm, line))

        # 滑動窗口提取 chunks
        for i in range(len(normalized) - self.min_lines + 1):
            chunk_lines = normalized[i:i + self.min_lines]
            start_line = chunk_lines[0][0]

            # 計算 hash
            chunk_text = "\n".join(line[1] for line in chunk_lines)
            chunk_hash = hashlib.md5(chunk_text.encode()).hexdigest()

            # 原始程式碼
            original_lines = [line[2] for line in chunk_lines]

            chunks.append((start_line, chunk_hash, original_lines))

        return chunks

    def scan_directory(self) -> list[str]:
        """掃描目錄"""
        files = []
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if not self._should_skip(rel_path):
                    files.append(rel_path)
        return files

    def analyze(self) -> DuplicateReport:
        """執行分析"""
        report = DuplicateReport()

        files = self.scan_directory()
        report.total_files = len(files)

        # 第一遍：建立 chunk 索引
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

        # 第二遍：找出重複
        seen_pairs = set()
        duplicates_raw = []

        for chunk_hash, locations in self.chunk_index.items():
            if len(locations) < 2:
                continue

            # 找出所有配對
            for i, (file1, start1, lines1) in enumerate(locations):
                for file2, start2, lines2 in locations[i + 1:]:
                    # 跳過同一檔案內相鄰的重複
                    if file1 == file2 and abs(start1 - start2) < self.min_lines:
                        continue

                    # 去重
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

        # 合併相鄰的重複區塊
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

        # 按行數排序
        report.duplicate_blocks.sort(key=lambda x: x.lines, reverse=True)

        return report

    def _merge_adjacent(self, duplicates: list[dict]) -> list[dict]:
        """合併相鄰的重複區塊"""
        if not duplicates:
            return []

        # 按檔案和起始行排序
        duplicates.sort(key=lambda x: (x["file1"], x["file2"], x["start1"], x["start2"]))

        merged = []
        current = None

        for dup in duplicates:
            if current is None:
                current = dup.copy()
                continue

            # 檢查是否相鄰
            same_files = (current["file1"] == dup["file1"] and current["file2"] == dup["file2"])
            adjacent1 = abs(dup["start1"] - current["end1"]) <= 2
            adjacent2 = abs(dup["start2"] - current["end2"]) <= 2

            if same_files and adjacent1 and adjacent2:
                # 合併
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
        """印出報告"""
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
                        print(f"    | ...")
        else:
            print("\n  No significant duplicates found")


def detect_duplicates(project_path: Path, min_lines: int = 6) -> DuplicateReport:
    """便捷函數"""
    detector = DuplicateDetector(project_path, min_lines)
    return detector.analyze()
