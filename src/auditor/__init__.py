"""
LLM Auditor - Let AI understand what each file does

Flow:
1. After scanning files, let LLM generate descriptions
2. High-level (L0): What the entire file does (one sentence)
3. Mid-level (L1): Main features, APIs, dependencies
4. Detail (L2): Purpose of each function/component

This way, when a user says "I want to build an e-commerce feature", AI can find related modules.
"""

from .llm_auditor import LLMAuditor, audit_file, audit_project

__all__ = ["LLMAuditor", "audit_file", "audit_project"]
