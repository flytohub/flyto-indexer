"""Tests for mapper/project_map module."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from mapper.project_map import (
    FileInfo, ProjectMapGenerator,
    PATH_CATEGORY_MAP, FILENAME_PURPOSE_MAP,
    search_project_map,
)


class TestFileInfo:
    """Test FileInfo dataclass."""

    def test_creation(self):
        info = FileInfo(
            path="src/auth.py",
            category="api",
            purpose="Authentication",
            exports=["login", "logout"],
            imports=["jwt"],
            lines=50,
        )
        assert info.path == "src/auth.py"
        assert info.category == "api"
        assert len(info.exports) == 2


class TestProjectMapGeneratorCategory:
    """Test file categorization."""

    def test_api_path(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        assert gen._infer_category("src/api/auth.py") == "api"

    def test_routes_path(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        assert gen._infer_category("src/routes/user.ts") == "api"

    def test_service_path(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        assert gen._infer_category("src/services/payment.py") == "service"

    def test_component_path(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        assert gen._infer_category("src/components/Button.vue") == "component"

    def test_composable_path(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        assert gen._infer_category("src/composables/useAuth.ts") == "composable"

    def test_store_path(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        assert gen._infer_category("src/stores/user.ts") == "store"

    def test_test_path(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        assert gen._infer_category("tests/test_auth.py") == "test"

    def test_util_path(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        assert gen._infer_category("src/utils/format.ts") == "util"

    def test_config_path(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        assert gen._infer_category("src/config/database.py") == "config"

    def test_default_module(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        assert gen._infer_category("src/engine.py") == "module"


class TestProjectMapGeneratorPurpose:
    """Test purpose inference."""

    def test_purpose_from_filename(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        purpose = gen._infer_purpose("src/auth.py", [], [])
        assert "authentication" in purpose.lower()

    def test_purpose_from_exports(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        purpose = gen._infer_purpose("src/utils.py", ["formatDate", "parseJSON"], [])
        assert "formatDate" in purpose or "parseJSON" in purpose

    def test_purpose_from_classes(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        purpose = gen._infer_purpose("src/something.py", [], ["PaymentGateway"])
        assert "paymentgateway" in purpose.lower()


class TestProjectMapGeneratorAnalyzePython:
    """Test Python file analysis."""

    def test_exports_functions(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        code = "def login():\n    pass\n\ndef logout():\n    pass\n"
        exports, imports, classes = gen.analyze_python(code)
        assert "login" in exports
        assert "logout" in exports

    def test_exports_classes(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        code = "class UserService:\n    pass\n"
        exports, imports, classes = gen.analyze_python(code)
        assert "UserService" in exports
        assert "UserService" in classes

    def test_detects_imports(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        code = "import os\nfrom pathlib import Path\n"
        exports, imports, classes = gen.analyze_python(code)
        assert "os" in imports
        assert "pathlib" in imports

    def test_skips_private(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        code = "def _private():\n    pass\n"
        exports, imports, classes = gen.analyze_python(code)
        assert "_private" not in exports


class TestProjectMapGeneratorAnalyzeTypeScript:
    """Test TypeScript analysis."""

    def test_exports_function(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        code = "export function greet(name: string) {\n  return name;\n}\n"
        exports, imports, classes = gen.analyze_typescript(code)
        assert "greet" in exports

    def test_exports_class(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        code = "export class ApiClient {\n}\n"
        exports, imports, classes = gen.analyze_typescript(code)
        assert "ApiClient" in exports
        assert "ApiClient" in classes

    def test_detects_imports(self):
        gen = ProjectMapGenerator(Path("/tmp"))
        code = "import { ref } from 'vue';\nimport axios from 'axios';\n"
        exports, imports, classes = gen.analyze_typescript(code)
        assert "vue" in imports
        assert "axios" in imports


class TestProjectMapGeneratorGenerate:
    """Test full project map generation."""

    def test_generate_with_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "src"
            src.mkdir()
            (src / "main.py").write_text("def run():\n    pass\n")
            (src / "utils.py").write_text("def format():\n    pass\n")
            gen = ProjectMapGenerator(root)
            result = gen.generate()
            assert result["total_files"] == 2
            assert len(result["files"]) == 2
            assert len(result["categories"]) > 0

    def test_generate_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ProjectMapGenerator(Path(tmpdir))
            result = gen.generate()
            assert result["total_files"] == 0


class TestSearchProjectMap:
    """Test search_project_map function."""

    def test_search_by_path(self):
        project_map = {
            "files": {
                "src/auth.py": {"purpose": "Authentication", "category": "api", "exports": ["login"]},
                "src/utils.py": {"purpose": "Utility functions", "category": "util", "exports": ["format"]},
            }
        }
        results = search_project_map(project_map, "auth")
        assert len(results) >= 1
        assert results[0]["path"] == "src/auth.py"

    def test_search_by_purpose(self):
        project_map = {
            "files": {
                "src/payment.py": {"purpose": "payment processing", "category": "service", "exports": []},
            }
        }
        results = search_project_map(project_map, "payment")
        assert len(results) >= 1

    def test_search_by_export(self):
        project_map = {
            "files": {
                "src/auth.py": {"purpose": "auth", "category": "api", "exports": ["verifyToken"]},
            }
        }
        results = search_project_map(project_map, "verifyToken")
        assert len(results) >= 1

    def test_search_no_results(self):
        project_map = {"files": {}}
        results = search_project_map(project_map, "nonexistent")
        assert results == []

    def test_search_limit(self):
        project_map = {
            "files": {
                f"src/mod_{i}.py": {"purpose": "module", "category": "module", "exports": ["module"]}
                for i in range(20)
            }
        }
        results = search_project_map(project_map, "module", limit=5)
        assert len(results) <= 5
