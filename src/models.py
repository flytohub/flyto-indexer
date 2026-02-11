"""
Core data models for Flyto Indexer.

Symbol ID format: project:path:type:name
Example: flyto-cloud:src/pages/TopUp.vue:component:TopUp
"""

import hashlib
from dataclasses import dataclass, field
from enum import Enum


class SymbolType(str, Enum):
    """Symbol type (grade)"""
    FILE = "file"           # Entire file
    CLASS = "class"         # Class
    FUNCTION = "function"   # Function
    METHOD = "method"       # Class method
    COMPONENT = "component" # Vue/React component
    COMPOSABLE = "composable"  # Vue composable
    STORE = "store"         # Pinia/Vuex store
    ROUTE = "route"         # Route definition
    API = "api"             # API endpoint
    VARIABLE = "variable"   # Constant/variable
    TYPE = "type"           # TypeScript type definition
    INTERFACE = "interface" # Interface definition


class DependencyType(str, Enum):
    """Dependency type"""
    IMPORTS = "imports"       # A imports B
    CALLS = "calls"           # A calls B
    EXTENDS = "extends"       # A extends B
    IMPLEMENTS = "implements" # A implements B
    USES = "uses"             # A uses B (composable/store)
    ROUTES_TO = "routes_to"   # route points to component
    API_CALLS = "api_calls"   # frontend calls backend API


@dataclass
class Symbol:
    """
    Symbol (student ID) - a unique unit in the codebase

    ID format: project:path:type:name
    Like a student ID: school_grade_class_seat
    - project = school
    - path = class (file path)
    - type = grade (symbol type)
    - name = seat (symbol name)
    """
    project: str          # Project name
    path: str             # Relative path
    symbol_type: SymbolType  # Symbol type
    name: str             # Symbol name

    # Location info
    start_line: int = 0
    end_line: int = 0

    # Content (used for hash computation and embedding generation)
    content: str = ""
    content_hash: str = ""

    # Summary (used for L1)
    summary: str = ""

    # Metadata
    language: str = ""
    exports: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    returns: str = ""

    # Reference count (used for search ranking)
    reference_count: int = 0

    @property
    def id(self) -> str:
        """Generate unique Symbol ID"""
        return f"{self.project}:{self.path}:{self.symbol_type.value}:{self.name}"

    @property
    def short_id(self) -> str:
        """Short ID (without project prefix)"""
        return f"{self.path}:{self.symbol_type.value}:{self.name}"

    def compute_hash(self) -> str:
        """Compute content hash"""
        self.content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]
        return self.content_hash

    def to_dict(self, include_content: bool = True, compact: bool = False) -> dict:
        """
        Convert to dict.

        Args:
            include_content: Include content field (set False for main index)
            compact: Skip empty fields to reduce size
        """
        result = {
            "project": self.project,
            "path": self.path,
            "type": self.symbol_type.value,
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "language": self.language,
        }

        # Include content only when requested
        if include_content:
            result["content"] = self.content

        # Always include content_hash for change detection
        if self.content_hash:
            result["content_hash"] = self.content_hash

        # Compact mode: skip empty fields
        if compact:
            if self.summary:
                result["summary"] = self.summary
            if self.exports:
                result["exports"] = self.exports
            if self.imports:
                result["imports"] = self.imports
            if self.params:
                result["params"] = self.params
            if self.returns:
                result["returns"] = self.returns
            if self.reference_count > 0:
                result["ref_count"] = self.reference_count
        else:
            result["summary"] = self.summary
            result["exports"] = self.exports
            result["imports"] = self.imports
            result["ref_count"] = self.reference_count

        return result

    def to_content_record(self) -> dict:
        """Return minimal record for content.jsonl."""
        return {
            "id": self.id,
            "content": self.content,
        }


@dataclass
class Dependency:
    """
    Dependency (edge in causality graph)

    source -> target (type)
    Example: TopUp.vue -calls-> useWallet.topUp()
    """
    source_id: str        # Source Symbol ID
    target_id: str        # Target Symbol ID
    dep_type: DependencyType

    # Source location (which line contains the reference)
    source_line: int = 0

    # Additional info
    metadata: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.source_id}--{self.dep_type.value}-->{self.target_id}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source_id,
            "target": self.target_id,
            "type": self.dep_type.value,
            "line": self.source_line,
            "metadata": self.metadata,
        }


@dataclass
class FileManifest:
    """
    File fingerprint (used for change detection)
    """
    path: str
    content_hash: str
    line_count: int
    symbols: list[str] = field(default_factory=list)  # Symbol IDs
    last_indexed: str = ""  # ISO timestamp

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "hash": self.content_hash,
            "lines": self.line_count,
            "symbols": self.symbols,
            "indexed_at": self.last_indexed,
        }


@dataclass
class ProjectIndex:
    """
    Project index (L0 outline)
    """
    project: str
    root_path: str

    # Directory structure
    tree: dict = field(default_factory=dict)

    # File manifest (path -> FileManifest)
    files: dict[str, FileManifest] = field(default_factory=dict)

    # All symbols (id -> Symbol)
    symbols: dict[str, Symbol] = field(default_factory=dict)

    # Dependencies (id -> Dependency)
    dependencies: dict[str, Dependency] = field(default_factory=dict)

    # Entry points
    entry_points: list[str] = field(default_factory=list)

    # Route table (path -> component)
    routes: dict[str, str] = field(default_factory=dict)

    # API endpoints
    api_endpoints: list[dict] = field(default_factory=list)

    # Reverse index (symbol_id -> referenced by whom)
    reverse_index: dict[str, list[str]] = field(default_factory=dict)

    def get_affected_by(self, symbol_id: str) -> list[str]:
        """
        Reverse lookup: if this symbol is changed, which other symbols are affected?

        Like changing a seat number and tracing back which classes/grades are impacted.
        """
        affected = []
        for dep in self.dependencies.values():
            if dep.target_id == symbol_id:
                affected.append(dep.source_id)
        return affected

    def get_depends_on(self, symbol_id: str) -> list[str]:
        """
        Forward lookup: which other symbols does this symbol depend on?
        """
        depends = []
        for dep in self.dependencies.values():
            if dep.source_id == symbol_id:
                depends.append(dep.target_id)
        return depends

    def get_impact_chain(self, symbol_id: str, max_depth: int = 3) -> dict:
        """
        Get the full impact chain (recursive)

        Changed useWallet.topUp()
          -> L1: TopUp.vue, WalletPage.vue (direct callers)
          -> L2: /wallet route (references TopUp.vue)
          -> L3: App.vue (contains router-view)
        """
        result = {"symbol": symbol_id, "levels": []}
        visited = {symbol_id}
        current_level = [symbol_id]

        for depth in range(max_depth):
            next_level = []
            for sid in current_level:
                affected = self.get_affected_by(sid)
                for a in affected:
                    if a not in visited:
                        visited.add(a)
                        next_level.append(a)

            if next_level:
                result["levels"].append({
                    "depth": depth + 1,
                    "symbols": next_level,
                })
            current_level = next_level

            if not current_level:
                break

        return result
