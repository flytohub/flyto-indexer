"""Code analyzers"""
from .api_consistency import APIConsistencyChecker, APIConsistencyReport, check_api_consistency
from .complexity import ComplexityAnalyzer, ComplexityReport, analyze_complexity
from .coverage import CoverageAnalyzer, CoverageReport, analyze_coverage
from .dead_code import DeadCodeDetector, DeadCodeReport, detect_dead_code
from .duplicates import DuplicateDetector, DuplicateReport, detect_duplicates
from .security import SecurityReport, SecurityScanner, scan_security
from .stale_files import StaleFileDetector, StaleReport, detect_stale_files
from .layers import (
    LayerDef, LayerReport, LayerViolation,
    add_layer, check_layers, check_layers_dict, remove_layer,
)
from .rules import RulesChecker, RulesReport, RuleViolation, add_rule, check_rules, remove_rule
from .taint import TaintAnalyzer, TaintFlow
from .taint_dsl import (
    add_taint_sanitizer, add_taint_sink, add_taint_source,
    list_taint_rules, remove_taint_rule,
)

__all__ = [
    # Dead code
    "DeadCodeDetector", "DeadCodeReport", "detect_dead_code",
    # Stale files
    "StaleFileDetector", "StaleReport", "detect_stale_files",
    # Complexity
    "ComplexityAnalyzer", "ComplexityReport", "analyze_complexity",
    # Coverage
    "CoverageAnalyzer", "CoverageReport", "analyze_coverage",
    # Duplicates
    "DuplicateDetector", "DuplicateReport", "detect_duplicates",
    # API Consistency
    "APIConsistencyChecker", "APIConsistencyReport", "check_api_consistency",
    # Security
    "SecurityScanner", "SecurityReport", "scan_security",
    # Taint analysis
    "TaintAnalyzer", "TaintFlow",
    # Project rules
    "RulesChecker", "RulesReport", "RuleViolation",
    "add_rule", "check_rules", "remove_rule",
    # Architecture layers
    "LayerDef", "LayerReport", "LayerViolation",
    "add_layer", "check_layers", "check_layers_dict", "remove_layer",
    # Taint DSL (yaml CRUD; engine is TaintAnalyzer)
    "add_taint_source", "add_taint_sink", "add_taint_sanitizer",
    "remove_taint_rule", "list_taint_rules",
]
