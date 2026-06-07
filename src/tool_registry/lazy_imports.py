"""
Lazy import helpers for tool dispatch.

Each function returns a module on first call, avoiding circular imports
and keeping startup fast.
"""


def _search():
    try:
        from ..tools import search
    except ImportError:
        from tools import search
    return search


def _refs():
    try:
        from ..tools import references
    except ImportError:
        from tools import references
    return references


def _info():
    try:
        from ..tools import code_info
    except ImportError:
        from tools import code_info
    return code_info


def _maint():
    try:
        from ..tools import maintenance
    except ImportError:
        from tools import maintenance
    return maintenance


def _quality():
    try:
        from .. import quality
    except ImportError:
        import quality
    return quality


def _diff():
    try:
        from .. import diff_impact
    except ImportError:
        import diff_impact
    return diff_impact


def _task():
    try:
        from ..tools import task_analysis
    except ImportError:
        from tools import task_analysis
    return task_analysis


def _validation():
    try:
        from ..tools import validation
    except ImportError:
        from tools import validation
    return validation


def _git_intel():
    try:
        from ..tools import git_intel
    except ImportError:
        from tools import git_intel
    return git_intel


def _coverage_intel():
    try:
        from ..tools import coverage_intel
    except ImportError:
        from tools import coverage_intel
    return coverage_intel


def _type_contracts():
    try:
        from ..tools import type_contracts
    except ImportError:
        from tools import type_contracts
    return type_contracts


def _dep_scanner():
    try:
        from .. import dependency_scanner
    except ImportError:
        import dependency_scanner
    return dependency_scanner


def _profile():
    try:
        from .. import project_profile
    except ImportError:
        import project_profile
    return project_profile


def _secret_scanner():
    try:
        from .. import secret_scanner
    except ImportError:
        import secret_scanner
    return secret_scanner


def _license_scanner():
    try:
        from .. import license_scanner
    except ImportError:
        import license_scanner
    return license_scanner


def _doc_scanner():
    try:
        from .. import doc_scanner
    except ImportError:
        import doc_scanner
    return doc_scanner


def _verify():
    try:
        from .. import verify
    except ImportError:
        import verify
    return verify


def _pr_analyzer():
    try:
        from .. import pr_analyzer
    except ImportError:
        import pr_analyzer
    return pr_analyzer


def _framework_detector():
    try:
        from .. import framework_detector
    except ImportError:
        import framework_detector
    return framework_detector


def _smart():
    try:
        from ..tools import smart
    except ImportError:
        from tools import smart
    return smart


def _layers_mod():
    try:
        from ..analyzer import layers
    except ImportError:
        from analyzer import layers
    return layers


def _taint_dsl_mod():
    try:
        from ..analyzer import taint_dsl
    except ImportError:
        from analyzer import taint_dsl
    return taint_dsl
