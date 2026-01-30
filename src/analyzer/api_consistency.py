"""
API 格式一致性檢查

檢查 API 回應是否符合標準格式：
- 成功：{"ok": True, "data": ...}
- 失敗：{"ok": False, "error": "..."}

找出不符合規範的回應。
"""

import re
import ast
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class APIIssue:
    """API 格式問題"""
    file_path: str
    line: int
    issue_type: str  # wrong_key, missing_ok, inconsistent_case, etc.
    code: str
    suggestion: str


@dataclass
class APIConsistencyReport:
    """API 一致性報告"""
    total_files: int = 0
    total_returns: int = 0
    issues: list[APIIssue] = field(default_factory=list)

    @property
    def compliance_rate(self) -> float:
        if self.total_returns == 0:
            return 100
        return (self.total_returns - len(self.issues)) / self.total_returns * 100


class APIConsistencyChecker:
    """API 格式一致性檢查器"""

    # 標準格式
    VALID_SUCCESS_KEYS = {"ok", "data", "message", "meta", "pagination"}
    VALID_ERROR_KEYS = {"ok", "error", "error_code", "details"}

    # 常見錯誤格式
    WRONG_PATTERNS = {
        "success": "ok",
        "status": "ok",
        "result": "data",
        "results": "data",
        "msg": "message",
        "err": "error",
        "errMsg": "error",
        "error_message": "error",
        "errorMessage": "error",
    }

    def __init__(
        self,
        project_root: Path,
        api_dirs: list[str] = None,
        ignore_patterns: list[str] = None,
    ):
        self.project_root = project_root
        self.api_dirs = api_dirs or ["api", "routes", "routers", "endpoints", "views"]
        self.ignore_patterns = ignore_patterns or [
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".nuxt", ".output",
            "test", "tests", "__tests__",
        ]

    def _should_skip(self, path: str) -> bool:
        for pattern in self.ignore_patterns:
            if pattern in path:
                return True
        return False

    def _is_api_file(self, path: str) -> bool:
        """判斷是否為 API 檔案"""
        parts = Path(path).parts
        return any(d in parts for d in self.api_dirs)

    def scan_directory(self) -> list[str]:
        """掃描目錄"""
        files = []
        for ext in [".py", ".ts", ".js"]:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if not self._should_skip(rel_path) and self._is_api_file(rel_path):
                    files.append(rel_path)
        return files

    def analyze_python_file(self, rel_path: str, content: str) -> tuple[int, list[APIIssue]]:
        """分析 Python API 檔案"""
        issues = []
        return_count = 0

        lines = content.split("\n")

        # 找所有 return 語句
        for i, line in enumerate(lines):
            stripped = line.strip()

            # 跳過註解
            if stripped.startswith("#"):
                continue

            # 找 return {...}
            if "return" in stripped and "{" in stripped:
                return_count += 1
                issue = self._check_python_return(rel_path, i + 1, stripped)
                if issue:
                    issues.append(issue)

            # 找 JSONResponse({...})
            if "JSONResponse" in stripped and "{" in stripped:
                return_count += 1
                issue = self._check_python_return(rel_path, i + 1, stripped)
                if issue:
                    issues.append(issue)

        return return_count, issues

    def _check_python_return(self, file_path: str, line: int, code: str) -> Optional[APIIssue]:
        """檢查 Python return 語句"""
        # 提取字典部分
        dict_match = re.search(r'\{[^}]+\}', code)
        if not dict_match:
            return None

        dict_str = dict_match.group()

        # 檢查錯誤的 key
        for wrong_key, correct_key in self.WRONG_PATTERNS.items():
            pattern = rf'["\']?{wrong_key}["\']?\s*:'
            if re.search(pattern, dict_str, re.IGNORECASE):
                return APIIssue(
                    file_path=file_path,
                    line=line,
                    issue_type="wrong_key",
                    code=code[:80],
                    suggestion=f'Use "{correct_key}" instead of "{wrong_key}"',
                )

        # 檢查是否缺少 ok
        if '"ok"' not in dict_str and "'ok'" not in dict_str:
            # 但有 data 或 error
            has_data = '"data"' in dict_str or "'data'" in dict_str
            has_error = '"error"' in dict_str or "'error'" in dict_str

            if has_data or has_error:
                return APIIssue(
                    file_path=file_path,
                    line=line,
                    issue_type="missing_ok",
                    code=code[:80],
                    suggestion='Add "ok": True/False to the response',
                )

        return None

    def analyze_typescript_file(self, rel_path: str, content: str) -> tuple[int, list[APIIssue]]:
        """分析 TypeScript API 檔案"""
        issues = []
        return_count = 0

        lines = content.split("\n")

        for i, line in enumerate(lines):
            stripped = line.strip()

            # 跳過註解
            if stripped.startswith("//"):
                continue

            # 找 return {...} 或 res.json({...})
            if ("return" in stripped or "res.json" in stripped or "res.send" in stripped) and "{" in stripped:
                return_count += 1
                issue = self._check_ts_return(rel_path, i + 1, stripped)
                if issue:
                    issues.append(issue)

        return return_count, issues

    def _check_ts_return(self, file_path: str, line: int, code: str) -> Optional[APIIssue]:
        """檢查 TypeScript return 語句"""
        # 提取物件部分
        obj_match = re.search(r'\{[^}]+\}', code)
        if not obj_match:
            return None

        obj_str = obj_match.group()

        # 檢查錯誤的 key
        for wrong_key, correct_key in self.WRONG_PATTERNS.items():
            pattern = rf'\b{wrong_key}\s*:'
            if re.search(pattern, obj_str, re.IGNORECASE):
                return APIIssue(
                    file_path=file_path,
                    line=line,
                    issue_type="wrong_key",
                    code=code[:80],
                    suggestion=f'Use "{correct_key}" instead of "{wrong_key}"',
                )

        # 檢查是否缺少 ok
        if "ok:" not in obj_str and "ok :" not in obj_str:
            has_data = "data:" in obj_str or "data :" in obj_str
            has_error = "error:" in obj_str or "error :" in obj_str

            if has_data or has_error:
                return APIIssue(
                    file_path=file_path,
                    line=line,
                    issue_type="missing_ok",
                    code=code[:80],
                    suggestion='Add "ok: true/false" to the response',
                )

        return None

    def analyze(self) -> APIConsistencyReport:
        """執行分析"""
        report = APIConsistencyReport()

        files = self.scan_directory()
        report.total_files = len(files)

        for rel_path in files:
            full_path = self.project_root / rel_path
            try:
                content = full_path.read_text(encoding="utf-8")
            except Exception:
                continue

            ext = Path(rel_path).suffix

            if ext == ".py":
                count, issues = self.analyze_python_file(rel_path, content)
            else:
                count, issues = self.analyze_typescript_file(rel_path, content)

            report.total_returns += count
            report.issues.extend(issues)

        # 按檔案排序
        report.issues.sort(key=lambda x: (x.file_path, x.line))

        return report

    def print_report(self, report: APIConsistencyReport):
        """印出報告"""
        print(f"\n{'='*70}")
        print("API Consistency Check")
        print(f"{'='*70}")
        print(f"\nAPI files scanned: {report.total_files}")
        print(f"Return statements: {report.total_returns}")
        print(f"Issues found: {len(report.issues)}")
        print(f"Compliance rate: {report.compliance_rate:.1f}%")

        if report.issues:
            print(f"\n{'='*70}")
            print("API FORMAT ISSUES")
            print(f"{'='*70}")

            # 按類型分組
            by_type = {}
            for issue in report.issues:
                if issue.issue_type not in by_type:
                    by_type[issue.issue_type] = []
                by_type[issue.issue_type].append(issue)

            for issue_type, issues in by_type.items():
                print(f"\n[{issue_type.upper()}] {len(issues)} issues")

                for issue in issues[:10]:
                    print(f"\n  {issue.file_path}:{issue.line}")
                    print(f"  Code: {issue.code}")
                    print(f"  Fix: {issue.suggestion}")

                if len(issues) > 10:
                    print(f"\n  ... and {len(issues) - 10} more")
        else:
            print("\n  All API responses follow the standard format")


def check_api_consistency(project_path: Path) -> APIConsistencyReport:
    """便捷函數"""
    checker = APIConsistencyChecker(project_path)
    return checker.analyze()
