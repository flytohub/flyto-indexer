"""
安全掃描 - 找出潛在的安全問題

檢查項目：
1. 硬編碼的密鑰/密碼
2. SQL injection 風險
3. 未驗證的用戶輸入
4. 不安全的函數使用
5. 敏感資訊洩漏
"""

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SecurityIssue:
    """安全問題"""
    file_path: str
    line: int
    severity: str  # critical, high, medium, low
    category: str  # hardcoded_secret, sql_injection, etc.
    description: str
    code: str
    recommendation: str


@dataclass
class SecurityReport:
    """安全掃描報告"""
    total_files: int = 0
    issues: list[SecurityIssue] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return len([i for i in self.issues if i.severity == "critical"])

    @property
    def high_count(self) -> int:
        return len([i for i in self.issues if i.severity == "high"])


class SecurityScanner:
    """安全掃描器"""

    # 硬編碼密鑰模式
    SECRET_PATTERNS = [
        # API Keys (需要實際值，不是變數引用)
        (r'["\']?(?:api[_-]?key|apikey)["\']?\s*[=:]\s*["\']([a-zA-Z0-9_\-]{32,})["\']', "API Key"),
        (r'["\']?(?:secret[_-]?key|secretkey)["\']?\s*[=:]\s*["\']([a-zA-Z0-9_\-]{32,})["\']', "Secret Key"),
        (r'["\']?(?:access[_-]?token|accesstoken)["\']?\s*[=:]\s*["\']([a-zA-Z0-9_\-]{32,})["\']', "Access Token"),

        # AWS
        (r'AKIA[0-9A-Z]{16}', "AWS Access Key"),
        (r'["\']?(?:aws[_-]?secret)["\']?\s*[=:]\s*["\']([a-zA-Z0-9/+=]{40})["\']', "AWS Secret"),

        # Private Keys
        (r'-----BEGIN (?:RSA |DSA |EC )?PRIVATE KEY-----', "Private Key"),

        # JWT (完整的 JWT，不是片段)
        (r'["\']eyJ[a-zA-Z0-9_-]{20,}\.eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}["\']', "JWT Token"),

        # Database URLs with credentials (需要有實際密碼)
        (r'(?:mysql|postgres|mongodb)://[^:]+:[^@\s]{8,}@[^\s]+', "Database URL with credentials"),
    ]

    # 密碼模式（更嚴格）
    PASSWORD_PATTERN = r'["\']?(?:password|passwd|pwd)["\']?\s*[=:]\s*["\']([^"\']{8,})["\']'

    # SQL Injection 風險
    SQL_INJECTION_PATTERNS = [
        # Python
        (r'(?:execute|query|cursor\.execute)\s*\(\s*["\'][^"\']*%s', "SQL with string formatting"),
        (r'(?:execute|query)\s*\(\s*f["\']', "SQL with f-string"),
        (r'(?:execute|query)\s*\([^)]*\+\s*[a-zA-Z_]', "SQL with string concatenation"),

        # JavaScript/TypeScript
        (r'SELECT\s+.*\s+FROM\s+.*\s+WHERE\s+.*\$\{', "SQL with template literal"),
        (r'SELECT\s+.*\s+FROM\s+.*\s+WHERE\s+.*\'\s*\+', "SQL with string concatenation"),

        # Java
        (r'executeQuery\s*\([^)]*\+', "Java SQL with string concatenation"),
        (r'execute\s*\([^)]*\+', "Java SQL with string concatenation"),
        (r'createStatement.*execute', "Java createStatement (prefer PreparedStatement)"),
        (r'createQuery\s*\([^)]*\+', "Java JPA query with concatenation"),
        (r'["\']SELECT.*\+\s*\w+', "SQL string concatenation"),
        (r'["\']DELETE.*\+\s*\w+', "SQL string concatenation"),
        (r'["\']UPDATE.*\+\s*\w+', "SQL string concatenation"),
        (r'["\']INSERT.*\+\s*\w+', "SQL string concatenation"),

        # Go
        (r'db\.(?:Query|Exec)\s*\([^)]*\+', "Go SQL with string concatenation"),
        (r'fmt\.Sprintf.*(?:SELECT|INSERT|UPDATE|DELETE)', "Go SQL with fmt.Sprintf"),
    ]

    # 不安全函數（更精確）
    UNSAFE_FUNCTIONS = [
        # Python eval/exec - 排除 redis.eval, comment 中的 eval
        (r'(?<![.\w])eval\s*\(\s*["\']?[a-zA-Z_]', "eval()", "Arbitrary code execution risk"),
        (r'(?<![.\w])exec\s*\(\s*["\']?[a-zA-Z_]', "exec()", "Arbitrary code execution risk"),
        # Python 其他危險函數
        (r'pickle\.loads\s*\(', "pickle.loads()", "Deserialization vulnerability"),
        (r'yaml\.load\s*\([^)]*Loader\s*=\s*None', "yaml.load() without Loader", "Use yaml.safe_load() instead"),
        (r'yaml\.unsafe_load\s*\(', "yaml.unsafe_load()", "Use yaml.safe_load() instead"),
        (r'subprocess\.[a-z]+\s*\([^)]*shell\s*=\s*True', "subprocess with shell=True", "Command injection risk"),
        (r'os\.system\s*\([^)]*[+%]', "os.system() with string formatting", "Command injection risk"),
        (r'os\.popen\s*\([^)]*[+%]', "os.popen() with string formatting", "Command injection risk"),
        # JavaScript
        (r'\.innerHTML\s*=\s*[^"\'<\s]', "innerHTML with variable", "XSS vulnerability"),
        (r'dangerouslySetInnerHTML\s*=\s*\{', "dangerouslySetInnerHTML", "XSS vulnerability"),
        # Java
        (r'Runtime\.getRuntime\(\)\.exec\s*\([^)]*\+', "Runtime.exec() with concatenation", "Command injection risk"),
        (r'ObjectInputStream.*readObject', "Java deserialization", "Deserialization vulnerability"),
        (r'ScriptEngine.*eval\s*\(', "Java ScriptEngine.eval()", "Code injection risk"),
        # Go
        (r'exec\.Command\s*\([^)]*\+', "exec.Command with concatenation", "Command injection risk"),
        (r'template\.HTML\s*\(', "Go template.HTML()", "XSS vulnerability - bypasses escaping"),
    ]

    # 敏感資訊洩漏（更精確的模式）
    INFO_LEAK_PATTERNS = [
        # 只匹配真正的敏感資料洩漏
        (r'console\.log\s*\([^)]*(?:password|secret_key|api_key|access_token)\s*[,\)]', "Logging sensitive data"),
        (r'print\s*\([^)]*(?:password|secret_key|api_key|access_token)\s*[,\)]', "Printing sensitive data"),
    ]

    def __init__(
        self,
        project_root: Path,
        extensions: list[str] = None,
        ignore_patterns: list[str] = None,
    ):
        self.project_root = project_root
        self.extensions = extensions or [".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".java", ".go", ".env"]
        self.ignore_patterns = ignore_patterns or [
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".nuxt", ".output",
            "test", "tests", "__tests__", "mock", "fixture",
        ]

    def _should_skip(self, path: str) -> bool:
        for pattern in self.ignore_patterns:
            if pattern in path:
                return True
        return False

    def scan_directory(self) -> list[str]:
        """掃描目錄"""
        files = []
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if not self._should_skip(rel_path):
                    files.append(rel_path)
        return files

    def scan_file(self, rel_path: str, content: str) -> list[SecurityIssue]:
        """掃描單個檔案"""
        issues = []
        lines = content.split("\n")

        # 跳過明顯的非程式碼檔案
        if rel_path.endswith(('.md', '.txt', '.json', '.yaml', '.yml', '.lock')):
            return issues

        # 跳過編譯後的檔案和靜態資源
        skip_paths = ['/dist/', '/build/', '.min.', '/static/assets/', '/public/']
        if any(p in rel_path for p in skip_paths):
            return issues

        # 跳過 vendor 和 node_modules（雙重確認）
        if 'vendor' in rel_path or 'node_modules' in rel_path:
            return issues

        for i, line in enumerate(lines):
            line_num = i + 1

            # 跳過註解（但密鑰檢查不跳過）
            stripped = line.strip()
            is_comment = stripped.startswith("#") or stripped.startswith("//")

            # 1. 硬編碼密鑰（非密碼）
            for pattern, secret_type in self.SECRET_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    # 排除明顯的假值
                    if self._is_placeholder(line):
                        continue

                    issues.append(SecurityIssue(
                        file_path=rel_path,
                        line=line_num,
                        severity="critical",
                        category="hardcoded_secret",
                        description=f"Hardcoded {secret_type} detected",
                        code=self._mask_secret(line.strip()[:80]),
                        recommendation="Move to environment variable or secrets manager",
                    ))
                    break

            # 2. 密碼檢測（使用更嚴格的過濾）
            if re.search(self.PASSWORD_PATTERN, line, re.IGNORECASE):
                if not self._is_placeholder(line) and not self._is_password_false_positive(line):
                    issues.append(SecurityIssue(
                        file_path=rel_path,
                        line=line_num,
                        severity="critical",
                        category="hardcoded_secret",
                        description="Hardcoded Password detected",
                        code=self._mask_secret(line.strip()[:80]),
                        recommendation="Move to environment variable or secrets manager",
                    ))

            if is_comment:
                continue

            # 2. SQL Injection
            for pattern, desc in self.SQL_INJECTION_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    issues.append(SecurityIssue(
                        file_path=rel_path,
                        line=line_num,
                        severity="high",
                        category="sql_injection",
                        description=desc,
                        code=line.strip()[:80],
                        recommendation="Use parameterized queries instead",
                    ))
                    break

            # 3. 不安全函數
            for pattern, func_name, risk in self.UNSAFE_FUNCTIONS:
                if re.search(pattern, line):
                    # 過濾誤報
                    if self._is_unsafe_func_false_positive(line, func_name):
                        continue

                    issues.append(SecurityIssue(
                        file_path=rel_path,
                        line=line_num,
                        severity="high",
                        category="unsafe_function",
                        description=f"Unsafe function: {func_name}",
                        code=line.strip()[:80],
                        recommendation=risk,
                    ))
                    break

            # 4. 敏感資訊洩漏
            for pattern, desc in self.INFO_LEAK_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    issues.append(SecurityIssue(
                        file_path=rel_path,
                        line=line_num,
                        severity="medium",
                        category="info_leak",
                        description=desc,
                        code=line.strip()[:80],
                        recommendation="Remove sensitive data from logs",
                    ))
                    break

        return issues

    def _is_placeholder(self, line: str) -> bool:
        """檢查是否為佔位符或假值"""
        line_lower = line.lower()

        # 明確的佔位符
        placeholders = [
            "xxx", "your-", "example", "placeholder", "changeme",
            "todo", "fixme", "replace", "insert", "<your",
            "sk-xxx", "pk-xxx", "test_", "fake_", "mock_",
            "process.env", "os.environ", "os.getenv", "env.",
            "${", "{{", "}}", "params.", "config.",
        ]
        if any(p in line_lower for p in placeholders):
            return True

        return False

    def _is_password_false_positive(self, line: str) -> bool:
        """檢查密碼偵測是否為誤報"""
        line_lower = line.lower()

        # 1. HTML/Vue 的 type="password"
        if 'type=' in line_lower and 'password' in line_lower:
            if 'type="password"' in line_lower or "type='password'" in line_lower:
                return True
            if ':type=' in line_lower:  # Vue binding
                return True

        # 2. API endpoint 路徑（包含 password 但是 URL）
        if any(p in line_lower for p in ['/password', '-password', '_password:', 'password/']):
            if '://' not in line_lower:  # 不是帶密碼的 DB URL
                return True

        # 3. 類型定義（password: 'password' 這種映射）
        if re.search(r'password["\']?\s*:\s*["\']password', line_lower):
            return True

        # 4. 變數名稱定義（const password = ...）
        if re.search(r'(?:const|let|var)\s+password\s*=', line_lower):
            return True

        # 5. 函數參數或物件 key（password:, password=）後面接的是變數引用
        if re.search(r'password["\']?\s*[=:]\s*[a-zA-Z_][a-zA-Z0-9_.]*\s*[,\)\}]', line):
            return True

        # 6. i18n key 或常數定義
        if any(p in line_lower for p in ['password_', 'password.', '_password', 'password:']):
            if re.search(r'["\'][^"\']*password[^"\']*["\']', line_lower):
                # 值本身包含 password 字樣，可能是 i18n key
                return True

        # 7. Vue v-model 或 props
        if 'v-model' in line_lower or ':password' in line_lower or '@password' in line_lower:
            return True

        # 8. 日誌/錯誤訊息
        if any(p in line_lower for p in ['log(', 'error(', 'warn(', 'info(', 'debug(']):
            return True

        # 9. 明顯的測試值
        test_values = ['password123', 'test123', '12345678', 'testpassword', 'admin123']
        for tv in test_values:
            if tv in line_lower:
                return True

        return False

    def _is_unsafe_func_false_positive(self, line: str, func_name: str) -> bool:
        """檢查不安全函數偵測是否為誤報"""
        line_lower = line.lower()

        # 1. 註解中提到這些函數
        if line.strip().startswith(('#', '//', '*', '"""', "'''")):
            return True

        # 2. 字串中的說明文字（"without using eval"）
        if 'without' in line_lower or 'instead of' in line_lower or 'not use' in line_lower:
            return True

        # 3. 正則表達式模式（用於檢測）
        if re.search(r'["\'].*\\b' + func_name.replace('()', '') + r'.*["\']', line):
            return True
        if 'r"' in line or "r'" in line:  # raw string (regex pattern)
            return True

        # 4. Redis eval（不是 JS/Python eval）
        if 'redis' in line_lower and 'eval' in func_name:
            return True

        # 5. 測試檔案中的 assertion
        if 'assert' in line_lower or 'expect' in line_lower:
            return True

        # 6. 文件字串
        if '"""' in line or "'''" in line:
            return True

        return False

    def _mask_secret(self, line: str) -> str:
        """遮蔽敏感值"""
        # 簡單遮蔽引號內的長字串
        return re.sub(r'(["\'])([a-zA-Z0-9_\-/+=]{10})[a-zA-Z0-9_\-/+=]*\1', r'\1\2***\1', line)

    def analyze(self) -> SecurityReport:
        """執行分析"""
        report = SecurityReport()

        files = self.scan_directory()
        report.total_files = len(files)

        for rel_path in files:
            full_path = self.project_root / rel_path
            try:
                content = full_path.read_text(encoding="utf-8")
            except Exception:
                continue

            issues = self.scan_file(rel_path, content)
            report.issues.extend(issues)

        # 按嚴重性排序
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        report.issues.sort(key=lambda x: (severity_order.get(x.severity, 4), x.file_path, x.line))

        return report

    def print_report(self, report: SecurityReport):
        """印出報告"""
        print(f"\n{'='*70}")
        print("Security Scan Report")
        print(f"{'='*70}")
        print(f"\nFiles scanned: {report.total_files}")
        print(f"Issues found: {len(report.issues)}")
        print(f"  Critical: {report.critical_count}")
        print(f"  High: {report.high_count}")
        print(f"  Medium: {len([i for i in report.issues if i.severity == 'medium'])}")
        print(f"  Low: {len([i for i in report.issues if i.severity == 'low'])}")

        if report.issues:
            # 按類別分組
            by_category = {}
            for issue in report.issues:
                if issue.category not in by_category:
                    by_category[issue.category] = []
                by_category[issue.category].append(issue)

            for category, issues in by_category.items():
                print(f"\n{'='*70}")
                print(f"{category.upper().replace('_', ' ')} ({len(issues)} issues)")
                print(f"{'='*70}")

                for issue in issues[:10]:
                    icon = {"critical": "!!!", "high": "!!", "medium": "!", "low": "-"}
                    print(f"\n  [{issue.severity.upper()}] {issue.file_path}:{issue.line}")
                    print(f"  {icon.get(issue.severity, '-')} {issue.description}")
                    print(f"  Code: {issue.code}")
                    print(f"  Fix: {issue.recommendation}")

                if len(issues) > 10:
                    print(f"\n  ... and {len(issues) - 10} more")
        else:
            print("\n  No security issues found")


def scan_security(project_path: Path) -> SecurityReport:
    """便捷函數"""
    scanner = SecurityScanner(project_path)
    return scanner.analyze()
