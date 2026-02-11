"""
Security scanning - detect potential security issues

Checks:
1. Hardcoded secrets/passwords
2. SQL injection risks
3. Unvalidated user input
4. Unsafe function usage
5. Sensitive information leaks
"""

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SecurityIssue:
    """Security issue"""
    file_path: str
    line: int
    severity: str  # critical, high, medium, low
    category: str  # hardcoded_secret, sql_injection, etc.
    description: str
    code: str
    recommendation: str


@dataclass
class SecurityReport:
    """Security scan report"""
    total_files: int = 0
    issues: list[SecurityIssue] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return len([i for i in self.issues if i.severity == "critical"])

    @property
    def high_count(self) -> int:
        return len([i for i in self.issues if i.severity == "high"])


class SecurityScanner:
    """Security scanner"""

    # Hardcoded secret patterns
    SECRET_PATTERNS = [
        # API Keys (must be actual values, not variable references)
        (r'["\']?(?:api[_-]?key|apikey)["\']?\s*[=:]\s*["\']([a-zA-Z0-9_\-]{32,})["\']', "API Key"),
        (r'["\']?(?:secret[_-]?key|secretkey)["\']?\s*[=:]\s*["\']([a-zA-Z0-9_\-]{32,})["\']', "Secret Key"),
        (r'["\']?(?:access[_-]?token|accesstoken)["\']?\s*[=:]\s*["\']([a-zA-Z0-9_\-]{32,})["\']', "Access Token"),

        # AWS
        (r'AKIA[0-9A-Z]{16}', "AWS Access Key"),
        (r'["\']?(?:aws[_-]?secret)["\']?\s*[=:]\s*["\']([a-zA-Z0-9/+=]{40})["\']', "AWS Secret"),

        # Private Keys
        (r'-----BEGIN (?:RSA |DSA |EC )?PRIVATE KEY-----', "Private Key"),

        # JWT (complete JWT, not fragments)
        (r'["\']eyJ[a-zA-Z0-9_-]{20,}\.eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}["\']', "JWT Token"),

        # Database URLs with credentials (must contain actual passwords)
        (r'(?:mysql|postgres|mongodb)://[^:]+:[^@\s]{8,}@[^\s]+', "Database URL with credentials"),
    ]

    # Password patterns (stricter matching)
    PASSWORD_PATTERN = r'["\']?(?:password|passwd|pwd)["\']?\s*[=:]\s*["\']([^"\']{8,})["\']'

    # SQL Injection risks
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

    # Unsafe functions (more precise matching)
    UNSAFE_FUNCTIONS = [
        # Python eval/exec - exclude redis.eval and eval in comments
        (r'(?<![.\w])eval\s*\(\s*["\']?[a-zA-Z_]', "eval()", "Arbitrary code execution risk"),
        (r'(?<![.\w])exec\s*\(\s*["\']?[a-zA-Z_]', "exec()", "Arbitrary code execution risk"),
        # Other dangerous Python functions
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

    # Sensitive information leaks (more precise patterns)
    INFO_LEAK_PATTERNS = [
        # Only match actual sensitive data leaks
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
        """Scan directory for files"""
        files = []
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))
                if not self._should_skip(rel_path):
                    files.append(rel_path)
        return files

    def scan_file(self, rel_path: str, content: str) -> list[SecurityIssue]:
        """Scan a single file"""
        issues = []
        lines = content.split("\n")

        # Skip obviously non-code files
        if rel_path.endswith(('.md', '.txt', '.json', '.yaml', '.yml', '.lock')):
            return issues

        # Skip compiled files and static assets
        skip_paths = ['/dist/', '/build/', '.min.', '/static/assets/', '/public/']
        if any(p in rel_path for p in skip_paths):
            return issues

        # Skip vendor and node_modules (double check)
        if 'vendor' in rel_path or 'node_modules' in rel_path:
            return issues

        for i, line in enumerate(lines):
            line_num = i + 1

            # Skip comments (but not for secret detection)
            stripped = line.strip()
            is_comment = stripped.startswith("#") or stripped.startswith("//")

            # 1. Hardcoded secrets (excluding passwords)
            for pattern, secret_type in self.SECRET_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    # Exclude obvious placeholder values
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

            # 2. Password detection (with stricter filtering)
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

            # 3. Unsafe functions
            for pattern, func_name, risk in self.UNSAFE_FUNCTIONS:
                if re.search(pattern, line):
                    # Filter out false positives
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

            # 4. Sensitive information leaks
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
        """Check if value is a placeholder or dummy value"""
        line_lower = line.lower()

        # Obvious placeholders
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
        """Check if password detection is a false positive"""
        line_lower = line.lower()

        # 1. HTML/Vue type="password"
        if 'type=' in line_lower and 'password' in line_lower:
            if 'type="password"' in line_lower or "type='password'" in line_lower:
                return True
            if ':type=' in line_lower:  # Vue binding
                return True

        # 2. API endpoint paths (contain "password" but are URLs)
        if any(p in line_lower for p in ['/password', '-password', '_password:', 'password/']):
            if '://' not in line_lower:  # Not a DB URL with credentials
                return True

        # 3. Type definitions (password: 'password' style mappings)
        if re.search(r'password["\']?\s*:\s*["\']password', line_lower):
            return True

        # 4. Variable name declarations (const password = ...)
        if re.search(r'(?:const|let|var)\s+password\s*=', line_lower):
            return True

        # 5. Function params or object keys (password:, password=) followed by variable references
        if re.search(r'password["\']?\s*[=:]\s*[a-zA-Z_][a-zA-Z0-9_.]*\s*[,\)\}]', line):
            return True

        # 6. i18n keys or constant definitions
        if any(p in line_lower for p in ['password_', 'password.', '_password', 'password:']):
            if re.search(r'["\'][^"\']*password[^"\']*["\']', line_lower):
                # The value itself contains "password", likely an i18n key
                return True

        # 7. Vue v-model or props
        if 'v-model' in line_lower or ':password' in line_lower or '@password' in line_lower:
            return True

        # 8. Log/error messages
        if any(p in line_lower for p in ['log(', 'error(', 'warn(', 'info(', 'debug(']):
            return True

        # 9. Obvious test values
        test_values = ['password123', 'test123', '12345678', 'testpassword', 'admin123']
        for tv in test_values:
            if tv in line_lower:
                return True

        return False

    def _is_unsafe_func_false_positive(self, line: str, func_name: str) -> bool:
        """Check if unsafe function detection is a false positive"""
        line_lower = line.lower()

        # 1. These functions mentioned in comments
        if line.strip().startswith(('#', '//', '*', '"""', "'''")):
            return True

        # 2. Descriptive text in strings (e.g. "without using eval")
        if 'without' in line_lower or 'instead of' in line_lower or 'not use' in line_lower:
            return True

        # 3. Regex patterns (used for detection)
        if re.search(r'["\'].*\\b' + func_name.replace('()', '') + r'.*["\']', line):
            return True
        if 'r"' in line or "r'" in line:  # raw string (regex pattern)
            return True

        # 4. Redis eval (not JS/Python eval)
        if 'redis' in line_lower and 'eval' in func_name:
            return True

        # 5. Assertions in test files
        if 'assert' in line_lower or 'expect' in line_lower:
            return True

        # 6. Docstrings
        if '"""' in line or "'''" in line:
            return True

        return False

    def _mask_secret(self, line: str) -> str:
        """Mask sensitive values"""
        # Simple masking of long strings within quotes
        return re.sub(r'(["\'])([a-zA-Z0-9_\-/+=]{10})[a-zA-Z0-9_\-/+=]*\1', r'\1\2***\1', line)

    def analyze(self) -> SecurityReport:
        """Run the analysis"""
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

        # Sort by severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        report.issues.sort(key=lambda x: (severity_order.get(x.severity, 4), x.file_path, x.line))

        return report

    def print_report(self, report: SecurityReport):
        """Print the report"""
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
            # Group by category
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
    """Convenience function"""
    scanner = SecurityScanner(project_path)
    return scanner.analyze()
