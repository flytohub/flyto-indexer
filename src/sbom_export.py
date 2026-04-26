"""
SBOM Export — generate CycloneDX 1.5 JSON Software Bill of Materials.

Reads dependency data from dependency_scanner, license data from license_scanner,
and lockfile hashes to produce a comprehensive SBOM.

Pure Python stdlib, no external dependencies.

CycloneDX 1.5 spec: https://cyclonedx.org/docs/1.5/json/
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("flyto-indexer.sbom-export")

# Ecosystem to PURL type mapping
_ECOSYSTEM_PURL_TYPE = {
    "npm": "npm",
    "pypi": "pypi",
    "go": "golang",
    "cargo": "cargo",
    "maven": "maven",
    "composer": "composer",
    "gem": "gem",
    "docker": "docker",
    "pub": "pub",
    "swift": "swift",
    "nuget": "nuget",
    "hex": "hex",
}

# Ecosystem to package registry URL
_ECOSYSTEM_REGISTRY = {
    "npm": "https://www.npmjs.com/package/{name}",
    "pypi": "https://pypi.org/project/{name}/",
    "go": "https://pkg.go.dev/{name}",
    "cargo": "https://crates.io/crates/{name}",
    "maven": "https://central.sonatype.com/artifact/{name}",
    "composer": "https://packagist.org/packages/{name}",
    "gem": "https://rubygems.org/gems/{name}",
    "docker": "https://hub.docker.com/_/{name}",
    "pub": "https://pub.dev/packages/{name}",
    "swift": "https://swiftpackageindex.com/search?query={name}",
    "nuget": "https://www.nuget.org/packages/{name}",
    "hex": "https://hex.pm/packages/{name}",
}


_SKIP_DIRS = frozenset({
    "node_modules", ".git", "vendor", "__pycache__", "dist", "build",
    ".venv", "venv", ".pytest_cache", ".flyto-index", ".flyto",
    ".tox", ".mypy_cache", ".ruff_cache", "target", "out",
})

_LOCKFILE_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "go.sum", "Cargo.lock", "Gemfile.lock", "poetry.lock",
    "composer.lock",
})


def _find_lockfile_dirs(project_path: Path) -> list[Path]:
    """Find all directories containing lockfiles, skipping ignored dirs."""
    import os
    dirs = set()
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if fname in _LOCKFILE_NAMES:
                dirs.add(Path(dirpath))
    return sorted(dirs)


def _build_purl(name: str, version: str, ecosystem: str) -> str:
    """
    Build a Package URL (purl) string.

    Format: pkg:<type>/<namespace>/<name>@<version>
    See: https://github.com/package-url/purl-spec
    """
    purl_type = _ECOSYSTEM_PURL_TYPE.get(ecosystem, ecosystem)

    # Handle namespaced packages
    if ecosystem == "npm" and name.startswith("@"):
        # @scope/name -> pkg:npm/%40scope/name@version
        parts = name.split("/", 1)
        if len(parts) == 2:
            namespace = parts[0].lstrip("@")
            pkg_name = parts[1]
            purl = f"pkg:{purl_type}/%40{namespace}/{pkg_name}"
        else:
            purl = f"pkg:{purl_type}/{name}"
    elif ecosystem == "maven" and ":" in name:
        # group:artifact -> pkg:maven/group/artifact@version
        parts = name.split(":", 1)
        purl = f"pkg:{purl_type}/{parts[0]}/{parts[1]}"
    elif ecosystem == "go" and "/" in name:
        # golang modules keep the full path
        purl = f"pkg:{purl_type}/{name}"
    elif ecosystem == "composer" and "/" in name:
        # composer: vendor/package
        purl = f"pkg:{purl_type}/{name}"
    else:
        purl = f"pkg:{purl_type}/{name}"

    # Add version
    if version:
        # Use pinned version if available, otherwise the constraint
        clean_version = version.lstrip("^~>=!<= ")
        if clean_version:
            purl += f"@{clean_version}"

    return purl


def _scope_to_cyclonedx(scope: str) -> str:
    """Map dependency scope to CycloneDX scope value."""
    if scope in ("dev", "build"):
        return "optional"
    if scope in ("production", ""):
        return "required"
    return "optional"


def _bom_ref(name: str, ecosystem: str) -> str:
    """Generate a stable bom-ref for a component."""
    return f"{ecosystem}:{name}"


# ---------------------------------------------------------------------------
# Hash extraction from lockfiles
# ---------------------------------------------------------------------------

def _extract_npm_hashes(project_path: Path) -> dict[str, list[dict]]:
    """Extract integrity hashes from package-lock.json.

    Returns {package_name: [{"alg": "SHA-512", "content": "..."}]}
    """
    hashes: dict[str, list[dict]] = {}
    lock_path = project_path / "package-lock.json"
    if not lock_path.is_file():
        return hashes

    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return hashes

    # v2/v3 format
    for key, info in (data.get("packages") or {}).items():
        if not key.startswith("node_modules/") or not isinstance(info, dict):
            continue
        integrity = info.get("integrity", "")
        if not integrity:
            continue
        pkg_name = key.split("node_modules/")[-1]
        # integrity format: "sha512-base64..." or "sha1-base64..."
        parsed = _parse_integrity(integrity)
        if parsed:
            hashes[pkg_name] = parsed

    return hashes


def _extract_go_hashes(project_path: Path) -> dict[str, list[dict]]:
    """Extract hashes from go.sum.

    Returns {module_name: [{"alg": "SHA-256", "content": "..."}]}
    """
    hashes: dict[str, list[dict]] = {}
    gosum_path = project_path / "go.sum"
    if not gosum_path.is_file():
        return hashes

    try:
        content = gosum_path.read_text(encoding="utf-8")
    except OSError:
        return hashes

    # Format: module version hash
    # e.g.: golang.org/x/text v0.3.7 h1:aRYxNxv6i...=
    for line in content.splitlines():
        parts = line.strip().split()
        if len(parts) != 3:
            continue
        module = parts[0]
        h = parts[2]
        # go.sum uses h1: prefix (SHA-256)
        if h.startswith("h1:"):
            hashes.setdefault(module, []).append({
                "alg": "SHA-256",
                "content": h[3:],
            })

    return hashes


def _extract_cargo_hashes(project_path: Path) -> dict[str, list[dict]]:
    """Extract checksums from Cargo.lock.

    Returns {package_name: [{"alg": "SHA-256", "content": "..."}]}
    """
    hashes: dict[str, list[dict]] = {}
    lock_path = project_path / "Cargo.lock"
    if not lock_path.is_file():
        return hashes

    try:
        content = lock_path.read_text(encoding="utf-8")
    except OSError:
        return hashes

    # Parse [[package]] blocks
    current_name = ""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith('name = "'):
            current_name = line.split('"')[1]
        elif line.startswith('checksum = "') and current_name:
            checksum = line.split('"')[1]
            if checksum:
                hashes[current_name] = [{"alg": "SHA-256", "content": checksum}]

    return hashes


def _parse_integrity(integrity: str) -> list[dict]:
    """Parse Subresource Integrity (SRI) hash string.

    Format: "sha512-base64==" or "sha256-base64== sha384-base64=="
    """
    result = []
    for part in integrity.split():
        if "-" not in part:
            continue
        alg, _, digest = part.partition("-")
        alg_map = {
            "sha1": "SHA-1",
            "sha256": "SHA-256",
            "sha384": "SHA-384",
            "sha512": "SHA-512",
            "md5": "MD5",
        }
        mapped = alg_map.get(alg.lower())
        if mapped and digest:
            result.append({"alg": mapped, "content": digest})
    return result


# ---------------------------------------------------------------------------
# Dependency tree extraction from lockfiles
# ---------------------------------------------------------------------------

def _extract_npm_dep_tree(project_path: Path) -> dict[str, list[str]]:
    """Extract dependency relationships from package-lock.json.

    Returns {parent_package: [child_package, ...]}
    Root-level dependencies use the project name as parent.
    """
    tree: dict[str, list[str]] = {}
    lock_path = project_path / "package-lock.json"
    if not lock_path.is_file():
        return tree

    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return tree

    # v2/v3 format: each package entry can have its own dependencies
    packages = data.get("packages") or {}

    for key, info in packages.items():
        if not isinstance(info, dict):
            continue

        # Determine package name from key
        if key == "":
            pkg_name = ""  # root
        elif key.startswith("node_modules/"):
            pkg_name = key.split("node_modules/")[-1]
        else:
            continue

        # Collect all deps of this package
        child_deps = []
        for dep_section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            for dep_name in (info.get(dep_section) or {}):
                child_deps.append(dep_name)

        if child_deps:
            tree[pkg_name] = child_deps

    return tree


# ---------------------------------------------------------------------------
# License collection for SBOM components
# ---------------------------------------------------------------------------

def _collect_component_licenses(project_path: Path, deps: list) -> dict[str, str]:
    """Collect per-dependency license info.

    Returns {package_name: "license_id"} for dependencies where we can determine the license.
    """
    licenses: dict[str, str] = {}

    # npm: read from node_modules/*/package.json (search subdirectories)
    import os
    node_modules_dirs = []
    for dirpath, dirnames, _ in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        nm = Path(dirpath) / "node_modules"
        if nm.is_dir():
            node_modules_dirs.append(nm)

    for node_modules in node_modules_dirs:
        for dep in deps:
            if dep.ecosystem != "npm":
                continue
            license_key = f"npm:{dep.name}"
            if license_key in licenses:
                continue
            dep_pkg = node_modules / dep.name / "package.json"
            if dep_pkg.is_file():
                try:
                    data = json.loads(dep_pkg.read_text(encoding="utf-8"))
                    lic = data.get("license", "")
                    if isinstance(lic, dict):
                        lic = lic.get("type", "")
                    if lic:
                        licenses[license_key] = lic
                except (json.JSONDecodeError, OSError):
                    pass

    # Cargo.toml: workspace members might have license in their own Cargo.toml
    # For now, skip — Cargo.lock doesn't carry license info

    # pypi: check installed package metadata (dist-info)
    for venv_dir in ("venv", ".venv"):
        site_packages = _find_site_packages(project_path / venv_dir)
        if site_packages:
            for dep in deps:
                if dep.ecosystem != "pypi":
                    continue
                lic = _read_pypi_license(site_packages, dep.name)
                if lic:
                    licenses[f"pypi:{dep.name}"] = lic
            break

    return licenses


def _find_site_packages(venv_path: Path) -> Path | None:
    """Find site-packages directory in a venv."""
    if not venv_path.is_dir():
        return None
    # Unix: lib/pythonX.Y/site-packages
    # Windows: Lib/site-packages
    for candidate in venv_path.glob("**/site-packages"):
        if candidate.is_dir():
            return candidate
    return None


def _read_pypi_license(site_packages: Path, pkg_name: str) -> str:
    """Read license from installed Python package metadata."""
    # Normalize name: PEP 503 — lowercase, replace [-_.] with -
    normalized = re.sub(r"[-_.]+", "_", pkg_name).lower()

    # Try to find .dist-info directory
    for entry in site_packages.iterdir():
        if not entry.is_dir() or not entry.name.endswith(".dist-info"):
            continue
        dist_name = entry.name.rsplit("-", 1)[0]
        if re.sub(r"[-_.]+", "_", dist_name).lower() == normalized:
            metadata = entry / "METADATA"
            if metadata.is_file():
                try:
                    for line in metadata.read_text(encoding="utf-8").splitlines():
                        if line.startswith("License:"):
                            lic = line[len("License:"):].strip()
                            if lic and lic != "UNKNOWN":
                                return lic
                        # Also check License-Expression (PEP 639)
                        if line.startswith("License-Expression:"):
                            return line[len("License-Expression:"):].strip()
                except OSError:
                    pass
            break
    return ""


# ---------------------------------------------------------------------------
# Author/supplier extraction from manifests
# ---------------------------------------------------------------------------

def _extract_project_metadata(project_path: Path) -> dict:
    """Extract project-level metadata (author, description, homepage) from manifests."""
    meta: dict = {}

    # package.json
    pkg_json = project_path / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            if data.get("author"):
                author = data["author"]
                if isinstance(author, dict):
                    meta["author"] = author.get("name", "")
                elif isinstance(author, str):
                    meta["author"] = author
            if data.get("description"):
                meta["description"] = data["description"]
            if data.get("homepage"):
                meta["homepage"] = data["homepage"]
            if data.get("repository"):
                repo = data["repository"]
                if isinstance(repo, dict):
                    meta["vcs"] = repo.get("url", "")
                elif isinstance(repo, str):
                    meta["vcs"] = repo
        except (json.JSONDecodeError, OSError):
            pass

    # pyproject.toml
    pyproject = project_path / "pyproject.toml"
    if pyproject.is_file() and "author" not in meta:
        try:
            import tomllib
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project_data = data.get("project", {})
            authors = project_data.get("authors", [])
            if authors and isinstance(authors[0], dict):
                meta["author"] = authors[0].get("name", "")
            if project_data.get("description"):
                meta["description"] = project_data["description"]
            urls = project_data.get("urls", {})
            if urls.get("Homepage"):
                meta["homepage"] = urls["Homepage"]
            if urls.get("Repository"):
                meta["vcs"] = urls["Repository"]
        except Exception:
            pass

    # Cargo.toml
    cargo = project_path / "Cargo.toml"
    if cargo.is_file() and "author" not in meta:
        try:
            import tomllib
            data = tomllib.loads(cargo.read_text(encoding="utf-8"))
            pkg = data.get("package", {})
            authors = pkg.get("authors", [])
            if authors:
                meta["author"] = authors[0] if isinstance(authors[0], str) else ""
            if pkg.get("description"):
                meta["description"] = pkg["description"]
            if pkg.get("homepage"):
                meta["homepage"] = pkg["homepage"]
            if pkg.get("repository"):
                meta["vcs"] = pkg["repository"]
        except Exception:
            pass

    # go.mod — module path gives us the VCS URL
    gomod = project_path / "go.mod"
    if gomod.is_file() and "vcs" not in meta:
        try:
            content = gomod.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.startswith("module "):
                    module_path = line.split()[1]
                    host = module_path.split("/", 1)[0].lower()
                    if host in ("github.com", "gitlab.com"):
                        meta["vcs"] = f"https://{module_path}"
                    break
        except OSError:
            pass

    return meta


# ---------------------------------------------------------------------------
# Main export
# ---------------------------------------------------------------------------

def export_sbom_cyclonedx(project_path: str | Path, project_name: str = "") -> dict:
    """
    Export project dependencies as CycloneDX 1.5 JSON SBOM.

    Includes:
    - Component list with PURL, scope, license, hashes
    - Dependency graph (from lockfiles)
    - Project metadata (author, description, VCS)
    - External references (registry URLs)

    Args:
        project_path: Root directory of the project.
        project_name: Project name (default: directory name).

    Returns:
        CycloneDX 1.5 JSON dict.
    """
    project_path = Path(project_path).resolve()
    if not project_name:
        project_name = project_path.name

    # Scan dependencies
    try:
        try:
            from .dependency_scanner import scan_dependencies
        except ImportError:
            from dependency_scanner import scan_dependencies

        inventory = scan_dependencies(project_path)
    except Exception as e:
        logger.warning("Failed to scan dependencies: %s", e)
        inventory = None

    # Collect hashes from lockfiles (search subdirectories too)
    all_hashes: dict[str, list[dict]] = {}
    for lockfile_dir in _find_lockfile_dirs(project_path):
        all_hashes.update(_extract_npm_hashes(lockfile_dir))
        all_hashes.update(_extract_go_hashes(lockfile_dir))
        all_hashes.update(_extract_cargo_hashes(lockfile_dir))

    # Collect per-dependency licenses
    dep_licenses: dict[str, str] = {}
    if inventory:
        dep_licenses = _collect_component_licenses(project_path, inventory.dependencies)

    # Extract dependency tree (search subdirectories)
    npm_tree: dict[str, list[str]] = {}
    for lockfile_dir in _find_lockfile_dirs(project_path):
        npm_tree.update(_extract_npm_dep_tree(lockfile_dir))

    # Project metadata
    project_meta = _extract_project_metadata(project_path)

    # Project license
    try:
        try:
            from .license_scanner import scan_licenses
        except ImportError:
            from license_scanner import scan_licenses
        license_result = scan_licenses(project_path)
        project_license = license_result.project_license
    except Exception:
        project_license = ""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    serial_number = f"urn:uuid:{uuid.uuid4()}"

    # Build components list
    components = []
    dep_graph: list[dict] = []  # CycloneDX dependencies array
    seen_purls = set()

    # Root component dependencies (for dependency graph)
    root_deps: list[str] = []

    if inventory:
        for dep in inventory.dependencies:
            # Use pinned version if available, otherwise the version constraint
            version = dep.pinned_version or dep.version or ""
            purl = _build_purl(dep.name, version, dep.ecosystem)

            # Deduplicate by purl
            if purl in seen_purls:
                continue
            seen_purls.add(purl)

            ref = _bom_ref(dep.name, dep.ecosystem)

            component: dict = {
                "type": "library",
                "bom-ref": ref,
                "name": dep.name,
                "version": version,
                "purl": purl,
            }

            # Scope
            scope = _scope_to_cyclonedx(dep.scope)
            if scope:
                component["scope"] = scope

            # Group for Maven packages
            if dep.ecosystem == "maven" and ":" in dep.name:
                parts = dep.name.split(":", 1)
                component["group"] = parts[0]
                component["name"] = parts[1]

            # License
            license_key = f"{dep.ecosystem}:{dep.name}"
            lic = dep_licenses.get(license_key, "")
            if lic:
                component["licenses"] = [{"license": {"id": lic}}]

            # Hashes from lockfiles
            pkg_hashes = all_hashes.get(dep.name, [])
            if pkg_hashes:
                component["hashes"] = pkg_hashes

            # External references (package registry URL)
            registry_tpl = _ECOSYSTEM_REGISTRY.get(dep.ecosystem)
            if registry_tpl:
                ext_name = dep.name
                if dep.ecosystem == "maven" and ":" in dep.name:
                    ext_name = dep.name.replace(":", "/")
                component["externalReferences"] = [{
                    "type": "distribution",
                    "url": registry_tpl.format(name=ext_name),
                }]

            # Properties — source file, ecosystem
            component["properties"] = [
                {"name": "flyto:ecosystem", "value": dep.ecosystem},
                {"name": "flyto:source_file", "value": dep.source_file},
            ]
            if dep.scope not in ("production", ""):
                component["properties"].append(
                    {"name": "flyto:scope", "value": dep.scope}
                )

            components.append(component)

            # Track root-level dependencies
            if dep.scope in ("production", "dev", "peer", "optional", "build", ""):
                root_deps.append(ref)

    # Build dependency graph
    # Root component → its direct dependencies
    dep_graph.append({
        "ref": project_name,
        "dependsOn": root_deps,
    })

    # npm transitive dependencies (from package-lock.json tree)
    if npm_tree:
        for parent_name, children in npm_tree.items():
            if not parent_name:
                continue  # root is handled above
            parent_ref = _bom_ref(parent_name, "npm")
            child_refs = [_bom_ref(c, "npm") for c in children]
            # Only include if parent is actually in our component list
            if any(c.get("bom-ref") == parent_ref for c in components):
                dep_graph.append({
                    "ref": parent_ref,
                    "dependsOn": child_refs,
                })

    # Build metadata
    metadata: dict = {
        "timestamp": now,
        "tools": {
            "components": [
                {
                    "type": "application",
                    "name": "flyto-indexer",
                    "version": "2.9.0",
                    "supplier": {"name": "flyto"},
                }
            ]
        },
        "component": {
            "type": "application",
            "name": project_name,
            "bom-ref": project_name,
        },
    }

    # Add project license to metadata component
    if project_license and project_license != "UNKNOWN":
        metadata["component"]["licenses"] = [
            {"license": {"id": project_license}}
        ]

    # Add project description
    if project_meta.get("description"):
        metadata["component"]["description"] = project_meta["description"]

    # Add author/supplier
    if project_meta.get("author"):
        metadata["supplier"] = {"name": project_meta["author"]}

    # Add external references at document level
    ext_refs = []
    if project_meta.get("vcs"):
        vcs_url = project_meta["vcs"]
        # Normalize git+https:// or git:// to https://
        vcs_url = re.sub(r"^git\+", "", vcs_url)
        vcs_url = re.sub(r"^git://", "https://", vcs_url)
        vcs_url = re.sub(r"\.git$", "", vcs_url)
        ext_refs.append({"type": "vcs", "url": vcs_url})
    if project_meta.get("homepage"):
        ext_refs.append({"type": "website", "url": project_meta["homepage"]})

    # Build CycloneDX 1.5 document
    sbom: dict = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": serial_number,
        "version": 1,
        "metadata": metadata,
        "components": components,
        "dependencies": dep_graph,
    }

    if ext_refs:
        sbom["externalReferences"] = ext_refs

    return sbom


def format_sbom_json(sbom: dict) -> str:
    """Format SBOM as pretty-printed JSON string."""
    return json.dumps(sbom, indent=2, ensure_ascii=False)


def format_sbom_summary(sbom: dict) -> str:
    """Format a human-readable summary of the SBOM."""
    components = sbom.get("components", [])
    if not components:
        return "SBOM: 0 components"

    # Count by ecosystem
    eco_counts: dict[str, int] = {}
    licensed = 0
    hashed = 0
    for c in components:
        props = {p["name"]: p["value"] for p in c.get("properties", [])}
        eco = props.get("flyto:ecosystem", "unknown")
        eco_counts[eco] = eco_counts.get(eco, 0) + 1
        if c.get("licenses"):
            licensed += 1
        if c.get("hashes"):
            hashed += 1

    lines = [
        f"CycloneDX 1.5 SBOM — {len(components)} components",
        "",
    ]

    # Ecosystem breakdown
    for eco, count in sorted(eco_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {eco}: {count}")

    lines.append("")

    # Coverage
    dep_count = len(sbom.get("dependencies", [])) - 1  # exclude root
    lines.append(f"  Licenses resolved: {licensed}/{len(components)}")
    lines.append(f"  Integrity hashes:  {hashed}/{len(components)}")
    lines.append(f"  Dependency links:  {max(0, dep_count)}")

    # Project info
    meta = sbom.get("metadata", {})
    comp = meta.get("component", {})
    proj_lic = ""
    if comp.get("licenses"):
        proj_lic = comp["licenses"][0].get("license", {}).get("id", "")
    if proj_lic:
        lines.append(f"  Project license:   {proj_lic}")

    return "\n".join(lines)
