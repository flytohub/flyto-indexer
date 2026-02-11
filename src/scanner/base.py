"""
Base scanner class for code analysis.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generator
import hashlib

try:
    from ..models import Symbol, Dependency, FileManifest
except ImportError:
    from models import Symbol, Dependency, FileManifest


class BaseScanner(ABC):
    """
    Scanner base class

    Subclasses must implement:
    - scan_file(): Scan a single file and extract symbols and dependencies
    - supported_extensions: List of supported file extensions
    """

    supported_extensions: list[str] = []

    def __init__(self, project_name: str):
        self.project = project_name

    @abstractmethod
    def scan_file(self, file_path: Path, content: str) -> tuple[list[Symbol], list[Dependency]]:
        """
        Scan a single file

        Returns:
            (symbols, dependencies)
        """
        pass

    def can_scan(self, file_path: Path) -> bool:
        """Check if this file type is supported"""
        return file_path.suffix in self.supported_extensions

    def compute_file_hash(self, content: str) -> str:
        """Compute content hash for the file"""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def create_file_manifest(
        self,
        file_path: Path,
        content: str,
        symbols: list[Symbol]
    ) -> FileManifest:
        """Create file manifest"""
        from datetime import datetime
        return FileManifest(
            path=str(file_path),
            content_hash=self.compute_file_hash(content),
            line_count=len(content.splitlines()),
            symbols=[s.id for s in symbols],
            last_indexed=datetime.now().isoformat(),
        )

    def extract_imports(self, content: str) -> list[str]:
        """Extract import statements (subclasses may override)"""
        return []


class ScanResult:
    """Scan result container"""

    def __init__(self):
        self.symbols: list[Symbol] = []
        self.dependencies: list[Dependency] = []
        self.manifests: list[FileManifest] = []
        self.errors: list[dict] = []

    def add_file_result(
        self,
        symbols: list[Symbol],
        dependencies: list[Dependency],
        manifest: FileManifest
    ):
        self.symbols.extend(symbols)
        self.dependencies.extend(dependencies)
        self.manifests.append(manifest)

    def add_error(self, file_path: str, error: str):
        self.errors.append({"file": file_path, "error": error})

    def summary(self) -> dict:
        return {
            "files_scanned": len(self.manifests),
            "symbols_found": len(self.symbols),
            "dependencies_found": len(self.dependencies),
            "errors": len(self.errors),
        }
