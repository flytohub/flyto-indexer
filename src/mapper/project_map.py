"""
PROJECT_MAP 產生器 - 讓 AI 快速理解專案結構

不用 LLM，純靜態分析產生有意義的檔案描述：
1. 從路徑推斷分類 (api/, services/, components/)
2. 從 exports 推斷用途
3. 從 imports 推斷依賴
4. 產生一句話描述
"""

import ast
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class FileInfo:
    """檔案資訊"""
    path: str
    category: str
    purpose: str
    exports: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    lines: int = 0


# 路徑關鍵字 → 分類映射
PATH_CATEGORY_MAP = {
    # API / Routes
    "api": "api",
    "routes": "api",
    "routers": "api",
    "endpoints": "api",
    "controllers": "api",
    "handlers": "handler",

    # Services / Business Logic
    "services": "service",
    "service": "service",
    "usecases": "usecase",
    "domain": "domain",
    "business": "business",

    # Data
    "models": "model",
    "entities": "model",
    "schemas": "schema",
    "types": "type",
    "dto": "dto",
    "repository": "repository",
    "repositories": "repository",
    "dao": "dao",

    # UI
    "components": "component",
    "views": "view",
    "pages": "page",
    "layouts": "layout",
    "widgets": "widget",

    # State
    "stores": "store",
    "store": "store",
    "state": "state",
    "redux": "store",
    "composables": "composable",
    "hooks": "hook",

    # Utils
    "utils": "util",
    "helpers": "helper",
    "lib": "lib",
    "common": "common",
    "shared": "shared",

    # Config
    "config": "config",
    "settings": "config",
    "constants": "constant",

    # Middleware
    "middleware": "middleware",
    "middlewares": "middleware",
    "interceptors": "interceptor",

    # Testing
    "test": "test",
    "tests": "test",
    "__tests__": "test",
    "spec": "test",

    # Scripts
    "scripts": "script",
    "cli": "cli",
    "commands": "command",
    "migrations": "migration",
}

# 檔名關鍵字 → 用途
FILENAME_PURPOSE_MAP = {
    "auth": "authentication",
    "login": "login",
    "register": "registration",
    "user": "user management",
    "payment": "payment processing",
    "order": "order management",
    "cart": "shopping cart",
    "checkout": "checkout flow",
    "product": "product management",
    "search": "search functionality",
    "upload": "file upload",
    "download": "file download",
    "email": "email handling",
    "notification": "notifications",
    "webhook": "webhook handling",
    "socket": "websocket",
    "ws": "websocket",
    "cache": "caching",
    "queue": "job queue",
    "worker": "background worker",
    "scheduler": "task scheduling",
    "cron": "scheduled tasks",
    "logger": "logging",
    "monitor": "monitoring",
    "health": "health check",
    "metrics": "metrics collection",
    "error": "error handling",
    "exception": "exception handling",
    "validator": "validation",
    "serializer": "serialization",
    "parser": "parsing",
    "formatter": "formatting",
    "converter": "data conversion",
    "transformer": "data transformation",
    "factory": "object factory",
    "builder": "object builder",
    "adapter": "adapter pattern",
    "gateway": "external gateway",
    "client": "API client",
    "provider": "service provider",
    "manager": "resource management",
    "handler": "request/event handler",
    "processor": "data processing",
    "analyzer": "analysis",
    "generator": "code/data generation",
    "template": "templating",
    "render": "rendering",
    "index": "entry point",
    "main": "main entry",
    "app": "application entry",
    "server": "server setup",
    "router": "routing",
    "route": "route definitions",
    "database": "database operations",
    "db": "database operations",
    "storage": "data storage",
    "file": "file operations",
    "image": "image processing",
    "pdf": "PDF handling",
    "excel": "Excel handling",
    "csv": "CSV handling",
    "json": "JSON handling",
    "xml": "XML handling",
    "crypto": "cryptography",
    "encrypt": "encryption",
    "hash": "hashing",
    "token": "token management",
    "session": "session management",
    "permission": "permissions",
    "role": "role management",
    "acl": "access control",
    "i18n": "internationalization",
    "locale": "localization",
    "translation": "translations",
}


class ProjectMapGenerator:
    """PROJECT_MAP 產生器"""

    def __init__(
        self,
        project_root: Path,
        extensions: list[str] = None,
        ignore_patterns: list[str] = None,
    ):
        self.project_root = project_root
        self.extensions = extensions or [
            ".py", ".ts", ".tsx", ".js", ".jsx", ".vue",
            ".java", ".go", ".rb", ".php", ".cs",
        ]
        self.ignore_patterns = ignore_patterns or [
            "node_modules", "__pycache__", ".git", "dist", "build",
            ".venv", "venv", ".nuxt", ".output", "vendor",
            ".pytest_cache", "coverage", ".next",
        ]

    def _should_skip(self, path: str) -> bool:
        for pattern in self.ignore_patterns:
            if pattern in path:
                return True
        return False

    def _infer_category(self, rel_path: str) -> str:
        """從路徑推斷分類"""
        parts = Path(rel_path).parts

        # 檢查路徑中的關鍵字
        for part in parts:
            part_lower = part.lower()
            if part_lower in PATH_CATEGORY_MAP:
                return PATH_CATEGORY_MAP[part_lower]

        # 從檔名推斷
        stem = Path(rel_path).stem.lower()
        for keyword, category in PATH_CATEGORY_MAP.items():
            if keyword in stem:
                return category

        return "module"

    def _infer_purpose(self, rel_path: str, exports: list[str], classes: list[str]) -> str:
        """從路徑和 exports 推斷用途"""
        stem = Path(rel_path).stem.lower()
        parts = Path(rel_path).parts

        # 1. 從檔名關鍵字推斷
        purposes = []
        for keyword, purpose in FILENAME_PURPOSE_MAP.items():
            if keyword in stem:
                purposes.append(purpose)

        # 2. 從路徑推斷類型
        category = self._infer_category(rel_path)

        # 3. 組合描述
        if purposes:
            main_purpose = purposes[0]
        elif exports:
            # 從 exports 推斷
            main_purpose = self._purpose_from_exports(exports)
        elif classes:
            main_purpose = self._purpose_from_classes(classes)
        else:
            main_purpose = stem.replace("_", " ").replace("-", " ")

        # 組合完整描述
        category_prefix = {
            "api": "API endpoint:",
            "service": "Service:",
            "component": "Component:",
            "view": "View:",
            "page": "Page:",
            "model": "Model:",
            "schema": "Schema:",
            "store": "Store:",
            "composable": "Composable:",
            "hook": "Hook:",
            "util": "Utility:",
            "helper": "Helper:",
            "config": "Config:",
            "middleware": "Middleware:",
            "test": "Test:",
            "handler": "Handler:",
        }

        prefix = category_prefix.get(category, "")
        if prefix:
            return f"{prefix} {main_purpose}"
        return main_purpose.capitalize()

    def _purpose_from_exports(self, exports: list[str]) -> str:
        """從 exports 推斷用途"""
        if not exports:
            return "module"

        # 取前幾個有意義的 export
        meaningful = [e for e in exports if not e.startswith("_") and len(e) > 2][:3]
        if meaningful:
            return ", ".join(meaningful)
        return exports[0]

    def _purpose_from_classes(self, classes: list[str]) -> str:
        """從 classes 推斷用途"""
        if not classes:
            return "module"
        return classes[0]

    def analyze_python(self, content: str) -> tuple[list[str], list[str], list[str]]:
        """分析 Python 檔案"""
        exports = []
        imports = []
        classes = []
        functions = []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return [], [], []

        for node in ast.iter_child_nodes(tree):
            # Imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])

            # Exports
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    classes.append(node.name)
                    exports.append(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    functions.append(node.name)
                    exports.append(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        if target.id.isupper():  # 常數
                            exports.append(target.id)

        return exports, list(set(imports)), classes

    def analyze_typescript(self, content: str) -> tuple[list[str], list[str], list[str]]:
        """分析 TypeScript/JavaScript 檔案"""
        exports = []
        imports = []
        classes = []

        # Imports
        import_patterns = [
            r'import\s+(?:\{[^}]+\}|\*\s+as\s+\w+|\w+)\s+from\s+[\'"]([^\'"]+)[\'"]',
            r'import\s+[\'"]([^\'"]+)[\'"]',
            r'require\s*\([\'"]([^\'"]+)[\'"]\)',
        ]
        for pattern in import_patterns:
            for match in re.finditer(pattern, content):
                module = match.group(1)
                if not module.startswith("."):
                    imports.append(module.split("/")[0])
                else:
                    imports.append(Path(module).stem)

        # Exports
        export_patterns = [
            (r'export\s+(?:async\s+)?function\s+(\w+)', "function"),
            (r'export\s+(?:const|let|var)\s+(\w+)', "const"),
            (r'export\s+class\s+(\w+)', "class"),
            (r'export\s+interface\s+(\w+)', "interface"),
            (r'export\s+type\s+(\w+)', "type"),
            (r'export\s+default\s+(?:class\s+)?(\w+)', "default"),
        ]
        for pattern, kind in export_patterns:
            for match in re.finditer(pattern, content):
                name = match.group(1)
                if name and name != "function":
                    exports.append(name)
                    if kind == "class":
                        classes.append(name)

        return exports, list(set(imports)), classes

    def analyze_vue(self, content: str) -> tuple[list[str], list[str], list[str]]:
        """分析 Vue 檔案"""
        # 提取 script 區塊
        script_match = re.search(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
        if script_match:
            exports, imports, classes = self.analyze_typescript(script_match.group(1))
        else:
            exports, imports, classes = [], [], []

        # 組件名稱
        name_match = re.search(r'name:\s*[\'"](\w+)[\'"]', content)
        if name_match:
            exports.insert(0, name_match.group(1))

        return exports, imports, classes

    def analyze_java(self, content: str) -> tuple[list[str], list[str], list[str]]:
        """分析 Java 檔案"""
        exports = []
        imports = []
        classes = []

        # Imports
        for match in re.finditer(r'import\s+([\w.]+);', content):
            imports.append(match.group(1).split(".")[-1])

        # Public classes
        for match in re.finditer(r'public\s+(?:abstract\s+)?(?:class|interface|enum)\s+(\w+)', content):
            name = match.group(1)
            classes.append(name)
            exports.append(name)

        # Public methods
        for match in re.finditer(r'public\s+(?:static\s+)?(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(', content):
            name = match.group(1)
            if not name[0].isupper():  # 排除建構子
                exports.append(name)

        return exports, list(set(imports)), classes

    def analyze_go(self, content: str) -> tuple[list[str], list[str], list[str]]:
        """分析 Go 檔案"""
        exports = []
        imports = []
        classes = []

        # Imports
        import_block = re.search(r'import\s*\((.*?)\)', content, re.DOTALL)
        if import_block:
            for match in re.finditer(r'[\'"]([^\'"]+)[\'"]', import_block.group(1)):
                imports.append(match.group(1).split("/")[-1])
        for match in re.finditer(r'import\s+[\'"]([^\'"]+)[\'"]', content):
            imports.append(match.group(1).split("/")[-1])

        # Exported functions (首字母大寫)
        for match in re.finditer(r'func\s+(?:\([^)]+\)\s+)?([A-Z]\w*)\s*\(', content):
            exports.append(match.group(1))

        # Exported types
        for match in re.finditer(r'type\s+([A-Z]\w*)\s+(?:struct|interface)', content):
            name = match.group(1)
            classes.append(name)
            exports.append(name)

        return exports, list(set(imports)), classes

    def analyze_file(self, rel_path: str) -> Optional[FileInfo]:
        """分析單個檔案"""
        full_path = self.project_root / rel_path

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception:
            return None

        lines = len(content.split("\n"))
        ext = Path(rel_path).suffix

        # 根據語言分析
        if ext == ".py":
            exports, imports, classes = self.analyze_python(content)
        elif ext in [".ts", ".tsx", ".js", ".jsx"]:
            exports, imports, classes = self.analyze_typescript(content)
        elif ext == ".vue":
            exports, imports, classes = self.analyze_vue(content)
        elif ext == ".java":
            exports, imports, classes = self.analyze_java(content)
        elif ext == ".go":
            exports, imports, classes = self.analyze_go(content)
        else:
            exports, imports, classes = [], [], []

        category = self._infer_category(rel_path)
        purpose = self._infer_purpose(rel_path, exports, classes)

        return FileInfo(
            path=rel_path,
            category=category,
            purpose=purpose,
            exports=exports[:10],  # 限制數量
            imports=imports[:10],
            classes=classes,
            functions=[e for e in exports if e not in classes][:10],
            lines=lines,
        )

    def generate(self) -> dict:
        """產生 PROJECT_MAP"""
        files = {}
        categories = {}

        # 掃描所有檔案
        for ext in self.extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                rel_path = str(file_path.relative_to(self.project_root))

                if self._should_skip(rel_path):
                    continue

                info = self.analyze_file(rel_path)
                if info:
                    files[rel_path] = {
                        "purpose": info.purpose,
                        "category": info.category,
                        "exports": info.exports,
                        "imports": info.imports,
                        "lines": info.lines,
                    }

                    # 分類索引
                    if info.category not in categories:
                        categories[info.category] = []
                    categories[info.category].append(rel_path)

        return {
            "project": self.project_root.name,
            "total_files": len(files),
            "files": files,
            "categories": categories,
        }

    def generate_outline(self) -> str:
        """產生簡潔的大綱（給 AI 快速掃描）"""
        project_map = self.generate()

        lines = [
            f"# {project_map['project']} - {project_map['total_files']} files",
            "",
        ]

        # 按分類列出
        for category, paths in sorted(project_map["categories"].items()):
            lines.append(f"## [{category}] ({len(paths)} files)")
            for path in sorted(paths)[:20]:  # 每類最多 20 個
                info = project_map["files"][path]
                lines.append(f"  - {path}: {info['purpose']}")
            if len(paths) > 20:
                lines.append(f"  ... and {len(paths) - 20} more")
            lines.append("")

        return "\n".join(lines)


def generate_project_map(project_path: Path, output_path: Path = None) -> dict:
    """便捷函數：產生 PROJECT_MAP"""
    generator = ProjectMapGenerator(project_path)
    project_map = generator.generate()

    if output_path:
        output_path.write_text(json.dumps(project_map, indent=2, ensure_ascii=False))

    return project_map


def generate_outline(project_path: Path) -> str:
    """便捷函數：產生大綱"""
    generator = ProjectMapGenerator(project_path)
    return generator.generate_outline()


def search_project_map(project_map: dict, query: str, limit: int = 10) -> list[dict]:
    """搜尋 PROJECT_MAP

    Args:
        project_map: 已產生的 PROJECT_MAP
        query: 搜尋關鍵字（如 "payment", "auth login", "購物車"）
        limit: 最多返回幾個結果

    Returns:
        相關檔案列表，按相關度排序
    """
    query_words = query.lower().split()
    results = []

    for path, info in project_map.get("files", {}).items():
        score = 0
        matches = []

        # 搜尋路徑
        path_lower = path.lower()
        for word in query_words:
            if word in path_lower:
                score += 3
                matches.append(f"path contains '{word}'")

        # 搜尋 purpose
        purpose_lower = info.get("purpose", "").lower()
        for word in query_words:
            if word in purpose_lower:
                score += 2
                matches.append(f"purpose contains '{word}'")

        # 搜尋 exports
        exports_str = " ".join(info.get("exports", [])).lower()
        for word in query_words:
            if word in exports_str:
                score += 2
                matches.append(f"exports contains '{word}'")

        # 搜尋 category
        category = info.get("category", "").lower()
        for word in query_words:
            if word in category:
                score += 1
                matches.append(f"category is '{category}'")

        if score > 0:
            results.append({
                "path": path,
                "purpose": info.get("purpose", ""),
                "category": info.get("category", ""),
                "exports": info.get("exports", [])[:5],
                "score": score,
                "matches": matches,
            })

    # 按分數排序
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def quick_search(project_path: Path, query: str, limit: int = 10) -> list[dict]:
    """快速搜尋（自動載入或產生 PROJECT_MAP）"""
    map_file = project_path / ".flyto-index" / "PROJECT_MAP.json"

    if map_file.exists():
        project_map = json.loads(map_file.read_text())
    else:
        generator = ProjectMapGenerator(project_path)
        project_map = generator.generate()

    return search_project_map(project_map, query, limit)
