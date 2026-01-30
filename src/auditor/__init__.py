"""
LLM Auditor - 讓 AI 理解每個檔案在做什麼

流程：
1. 掃描檔案後，讓 LLM 生成描述
2. 大向（L0）：整個檔案在做什麼（一句話）
3. 中向（L1）：主要功能、API、依賴
4. 細項（L2）：每個 function/component 的用途

這樣用戶說「我要做商城功能」時，AI 能找到相關模組。
"""

from .llm_auditor import LLMAuditor, audit_file, audit_project

__all__ = ["LLMAuditor", "audit_file", "audit_project"]
