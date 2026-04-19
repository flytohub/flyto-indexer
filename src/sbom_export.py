"""
SBOM Export — generate CycloneDX 1.4 JSON Software Bill of Materials.

Reads dependency data from dependency_scanner and converts to CycloneDX format.
Pure Python stdlib, no external dependencies.
"""

import json
import logging
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
}


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


def export_sbom_cyclonedx(project_path: str | Path, project_name: str = "") -> dict:
    """
    Export project dependencies as CycloneDX 1.4 JSON SBOM.

    Args:
        project_path: Root directory of the project.
        project_name: Project name (default: directory name).

    Returns:
        CycloneDX 1.4 JSON dict.
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

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    serial_number = f"urn:uuid:{uuid.uuid4()}"

    # Build components list
    components = []
    if inventory:
        seen_purls = set()
        for dep in inventory.dependencies:
            # Use pinned version if available, otherwise the version constraint
            version = dep.pinned_version or dep.version or ""
            purl = _build_purl(dep.name, version, dep.ecosystem)

            # Deduplicate by purl
            if purl in seen_purls:
                continue
            seen_purls.add(purl)

            component = {
                "type": "library",
                "name": dep.name,
                "version": version,
                "purl": purl,
            }

            # Add scope
            scope = _scope_to_cyclonedx(dep.scope)
            if scope:
                component["scope"] = scope

            # Add group for Maven packages
            if dep.ecosystem == "maven" and ":" in dep.name:
                parts = dep.name.split(":", 1)
                component["group"] = parts[0]
                component["name"] = parts[1]

            components.append(component)

    # Build CycloneDX 1.4 document
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "serialNumber": serial_number,
        "version": 1,
        "metadata": {
            "timestamp": now,
            "tools": [
                {
                    "vendor": "flyto",
                    "name": "flyto-indexer",
                    "version": "2.7.0",
                }
            ],
            "component": {
                "type": "application",
                "name": project_name,
                "bom-ref": project_name,
            },
        },
        "components": components,
    }

    return sbom


def format_sbom_json(sbom: dict) -> str:
    """Format SBOM as pretty-printed JSON string."""
    return json.dumps(sbom, indent=2, ensure_ascii=False)
