"""
Scanner wrappers — each returns a dict, swallowing failures to keep profile robust.
"""

import logging
import subprocess
from pathlib import Path

from .index_extract import load_index_file

logger = logging.getLogger("flyto-indexer.profile")


def git_info(project_path: Path) -> dict:
    """Extract git metadata."""
    result = {"recent_authors": [], "last_commit_date": ""}

    try:
        proc = subprocess.run(
            ["git", "-C", str(project_path), "log", "--format=%aN", "-50"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            authors = sorted(set(proc.stdout.strip().split("\n")))
            result["recent_authors"] = authors

        proc = subprocess.run(
            ["git", "-C", str(project_path), "log", "-1", "--format=%aI"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            result["last_commit_date"] = proc.stdout.strip()

    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("Git info unavailable: %s", e)

    return result


def scan_deps(project_path: Path) -> dict:
    """Scan dependencies using the dependency scanner."""
    try:
        try:
            from ..dependency_scanner import scan_dependencies
        except ImportError:
            from dependency_scanner import scan_dependencies

        inventory = scan_dependencies(project_path)
        return inventory.to_dict()
    except Exception as e:
        logger.debug("Dependency scan failed: %s", e)
        return {}


def scan_secrets(project_path: Path) -> dict:
    try:
        try:
            from ..secret_scanner import scan_secrets as _scan_secrets
        except ImportError:
            from secret_scanner import scan_secrets as _scan_secrets
        r = _scan_secrets(project_path)
        return {
            "total_files_scanned": r.total_files_scanned,
            "total_findings": r.total_findings,
            "critical": r.critical, "high": r.high, "medium": r.medium,
        }
    except Exception as e:
        logger.debug("Secret scan failed: %s", e)
        return {}


def scan_code_vulnerabilities(project_path: Path) -> dict:
    try:
        try:
            from ..secret_scanner import scan_code_vulnerabilities as _scan_code_vulns
        except ImportError:
            from secret_scanner import scan_code_vulnerabilities as _scan_code_vulns
        return _scan_code_vulns(project_path)
    except Exception as e:
        logger.debug("Code vulnerability scan failed: %s", e)
        return {}


def scan_git_history(project_path: Path) -> dict:
    try:
        try:
            from ..git_secret_scanner import scan_git_history as _scan_git_hist
        except ImportError:
            from git_secret_scanner import scan_git_history as _scan_git_hist
        return _scan_git_hist(project_path)
    except Exception as e:
        logger.debug("Git history secret scan failed: %s", e)
        return {}


def scan_dockerfile(project_path: Path) -> dict:
    try:
        try:
            from ..dockerfile_scanner import scan_dockerfiles
        except ImportError:
            from dockerfile_scanner import scan_dockerfiles
        return scan_dockerfiles(project_path)
    except Exception as e:
        logger.debug("Dockerfile scan failed: %s", e)
        return {}


def scan_license(project_path: Path) -> dict:
    try:
        try:
            from ..license_scanner import scan_licenses
        except ImportError:
            from license_scanner import scan_licenses
        r = scan_licenses(project_path)
        return {
            "project_license": r.project_license,
            "project_license_file": r.project_license_file,
            "dependency_licenses": r.dependency_licenses,
            "copyleft_warning": r.copyleft_warning,
            "dependencies_without_license_count": len(r.dependencies_without_license),
        }
    except Exception as e:
        logger.debug("License scan failed: %s", e)
        return {}


def scan_documentation(project_path: Path) -> dict:
    try:
        try:
            from ..doc_scanner import scan_documentation as _scan_docs
        except ImportError:
            from doc_scanner import scan_documentation as _scan_docs
        r = _scan_docs(project_path)
        return {
            "overall_score": r.overall_score,
            "readme_score": r.readme_score,
            "readme_sections": r.readme_sections,
            "api_doc_coverage": r.api_doc_coverage,
            "module_doc_coverage": r.module_doc_coverage,
            "inline_doc_coverage": r.inline_doc_coverage,
            "has_env_example": r.has_env_example,
            "has_changelog": r.has_changelog,
            "has_contributing": r.has_contributing,
            "suggestions": r.suggestions,
        }
    except Exception as e:
        logger.debug("Documentation scan failed: %s", e)
        return {}


def scan_taint(project_path: Path) -> dict:
    try:
        try:
            from ..analyzer.taint import TaintAnalyzer
        except ImportError:
            from analyzer.taint import TaintAnalyzer

        index_dir = project_path / ".flyto-index"
        raw_index = load_index_file(index_dir) if index_dir.exists() else {}

        analyzer = TaintAnalyzer(project_path, index=raw_index)
        r = analyzer.analyze_full()
        unsanitized = [f for f in r.taint_flows if not f.sanitized]
        return {
            "total_sources": r.total_sources,
            "total_sinks": r.total_sinks,
            "unsanitized_flows": len(unsanitized),
            "sanitized_flows": r.sanitized_flows,
            "high_risk_count": r.high_risk_count,
        }
    except Exception as e:
        logger.debug("Taint analysis failed: %s", e)
        return {}


def scan_iac(project_path: Path) -> dict:
    default = {
        "total_findings": 0, "critical": 0, "high": 0, "medium": 0, "low": 0,
        "findings": [], "frameworks_detected": [],
    }
    try:
        try:
            from ..iac_scanner import scan_iac_to_dict
        except ImportError:
            from iac_scanner import scan_iac_to_dict
        return scan_iac_to_dict(project_path)
    except Exception as e:
        logger.debug("IaC scan failed: %s", e)
        return default


def scan_frameworks(project_path: Path) -> list:
    try:
        try:
            from ..framework_detector import detect_frameworks
        except ImportError:
            from framework_detector import detect_frameworks
        return [fw.to_dict() for fw in detect_frameworks(project_path)]
    except Exception as e:
        logger.debug("Framework detection failed: %s", e)
        return []


def check_license_policy(license_data: dict) -> list[dict]:
    issues: list[dict] = []
    try:
        try:
            from ..rule_loader import get_license_policies
        except ImportError:
            from rule_loader import get_license_policies
        policies = get_license_policies()
        dep_licenses = license_data.get("dependency_licenses", {})
        for lic_id, count in dep_licenses.items():
            if lic_id in policies.get("deny", set()):
                issues.append({
                    "license": lic_id, "risk_level": "critical",
                    "reason": f"License {lic_id} is in deny list", "count": count,
                })
            elif lic_id in policies.get("warn", set()):
                issues.append({
                    "license": lic_id, "risk_level": "high",
                    "reason": f"Copyleft license {lic_id} may force open-source derivatives",
                    "count": count,
                })
        if not policies.get("allow_unlicensed", False):
            unlicensed_count = license_data.get("dependencies_without_license_count", 0)
            if unlicensed_count > 0:
                issues.append({
                    "license": "UNLICENSED", "risk_level": "medium",
                    "reason": f"{unlicensed_count} dependencies have no detectable license",
                    "count": unlicensed_count,
                })
    except Exception as e:
        logger.debug("License policy check failed: %s", e)
    return issues
