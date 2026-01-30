"""
陳舊檔案偵測 - 用 git 歷史找出長期沒人動過的檔案

這比靜態 import 分析更實用，因為：
1. 如果一個檔案 6 個月沒人改過，很可能是死碼
2. 如果整個目錄都沒人動過，可能整個功能都廢棄了
3. 結合最後修改者，可以知道該問誰
"""

import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class StaleFile:
    """陳舊檔案"""
    path: str
    last_modified: datetime
    last_author: str
    days_since_modified: int
    commit_count: int = 0


@dataclass
class StaleReport:
    """陳舊分析報告"""
    total_files: int = 0
    stale_files: list[StaleFile] = field(default_factory=list)
    stale_dirs: list[tuple[str, int, int]] = field(default_factory=list)  # (dir, file_count, avg_days)
    never_committed: list[str] = field(default_factory=list)


class StaleFileDetector:
    """陳舊檔案偵測器"""

    def __init__(
        self,
        project_root: Path,
        stale_days: int = 180,  # 6 個月沒動過算陳舊
        extensions: list[str] = None,
        ignore_patterns: list[str] = None,
    ):
        self.project_root = project_root
        self.stale_days = stale_days
        self.extensions = extensions or [".py", ".ts", ".tsx", ".js", ".jsx", ".vue"]
        self.ignore_patterns = ignore_patterns or [
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".nuxt", ".output",
        ]

    def _should_skip(self, path: str) -> bool:
        for pattern in self.ignore_patterns:
            if pattern in path:
                return True
        return False

    def _run_git(self, args: list[str]) -> str:
        """執行 git 命令"""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.project_root)] + args,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout.strip()
        except Exception as e:
            return ""

    def get_file_history(self, rel_path: str) -> tuple[datetime, str, int]:
        """
        取得檔案的 git 歷史

        Returns:
            (last_modified, last_author, commit_count)
        """
        # 最後修改時間和作者
        log = self._run_git([
            "log", "-1",
            "--format=%ai|%an",
            "--", rel_path
        ])

        if not log or "|" not in log:
            return None, "", 0

        try:
            parts = log.split("|", 1)
            if len(parts) != 2:
                return None, "", 0

            date_str, author = parts
            # Parse: "2026-01-27 15:18:57 +0800"
            date_str = date_str.strip()
            # Remove timezone for simple parsing
            if " +" in date_str or " -" in date_str:
                date_str = date_str.rsplit(" ", 1)[0]
            last_modified = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except Exception as e:
            return None, "", 0

        # commit 次數
        count_output = self._run_git([
            "rev-list", "--count", "HEAD",
            "--", rel_path
        ])
        commit_count = int(count_output) if count_output.isdigit() else 0

        return last_modified, author.strip(), commit_count

    def scan_directory(self) -> list[str]:
        """掃描目錄"""
        files = []
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if not self._should_skip(rel_path):
                    files.append(rel_path)
        return files

    def analyze(self) -> StaleReport:
        """執行分析"""
        report = StaleReport()
        now = datetime.now()
        stale_threshold = now - timedelta(days=self.stale_days)

        # 目錄統計
        dir_stats = defaultdict(lambda: {"files": [], "total_days": 0})

        files = self.scan_directory()
        report.total_files = len(files)

        print(f"Analyzing {len(files)} files...")

        for i, rel_path in enumerate(files):
            if i % 50 == 0:
                print(f"  Progress: {i}/{len(files)}")

            last_modified, author, commit_count = self.get_file_history(rel_path)

            if last_modified is None:
                # 從未提交過的檔案
                report.never_committed.append(rel_path)
                continue

            days_since = (now - last_modified).days

            # 陳舊檔案
            if last_modified < stale_threshold:
                stale_file = StaleFile(
                    path=rel_path,
                    last_modified=last_modified,
                    last_author=author,
                    days_since_modified=days_since,
                    commit_count=commit_count,
                )
                report.stale_files.append(stale_file)

            # 目錄統計
            dir_path = str(Path(rel_path).parent)
            dir_stats[dir_path]["files"].append(rel_path)
            dir_stats[dir_path]["total_days"] += days_since

        # 計算陳舊目錄
        for dir_path, stats in dir_stats.items():
            file_count = len(stats["files"])
            avg_days = stats["total_days"] // file_count if file_count > 0 else 0
            if avg_days > self.stale_days:
                report.stale_dirs.append((dir_path, file_count, avg_days))

        # 排序
        report.stale_files.sort(key=lambda x: x.days_since_modified, reverse=True)
        report.stale_dirs.sort(key=lambda x: x[2], reverse=True)

        return report

    def print_report(self, report: StaleReport):
        """印出報告"""
        print(f"\n{'=' * 70}")
        print(f"Stale Files Analysis (>{self.stale_days} days without changes)")
        print(f"{'=' * 70}")
        print(f"\nTotal files: {report.total_files}")
        print(f"Stale files: {len(report.stale_files)}")
        print(f"Never committed: {len(report.never_committed)}")

        if report.never_committed:
            print(f"\n{'=' * 70}")
            print("NEVER COMMITTED (new files not in git)")
            print(f"{'=' * 70}")
            for f in report.never_committed[:10]:
                print(f"  📄 {f}")
            if len(report.never_committed) > 10:
                print(f"  ... and {len(report.never_committed) - 10} more")

        if report.stale_files:
            print(f"\n{'=' * 70}")
            print(f"STALE FILES (top 20 oldest)")
            print(f"{'=' * 70}")
            for sf in report.stale_files[:20]:
                print(f"  🕸️ {sf.path}")
                print(f"     Last modified: {sf.days_since_modified} days ago by {sf.last_author}")
                print(f"     Total commits: {sf.commit_count}")

        if report.stale_dirs:
            print(f"\n{'=' * 70}")
            print("STALE DIRECTORIES (entire directories that are old)")
            print(f"{'=' * 70}")
            for dir_path, file_count, avg_days in report.stale_dirs[:10]:
                print(f"  📁 {dir_path}/")
                print(f"     {file_count} files, avg {avg_days} days old")


def detect_stale_files(project_path: Path, stale_days: int = 180) -> StaleReport:
    """便捷函數"""
    detector = StaleFileDetector(project_path, stale_days)
    return detector.analyze()
