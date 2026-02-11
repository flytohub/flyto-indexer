"""
LLM Auditor - Use LLM to understand code purpose

Core features:
1. audit_file() - Audit a single file, generate a purpose description
2. audit_project() - Audit an entire project, generate PROJECT_MAP
3. Incremental audit - Only audit files that have changed

Output format (stored in vector database):
{
    "path": "src/pages/TopUp.vue",
    "purpose": "Top-up page - displays plan list, handles payment, redirects to success page",
    "category": "payment",
    "keywords": ["top-up", "payment", "wallet", "topup"],
    "apis": ["/api/wallet/topup", "/api/wallet/plans"],
    "dependencies": ["useWallet", "usePayment"],
    "ui_elements": ["plan card", "payment button", "loading state"]
}
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Audit prompt
AUDIT_FILE_PROMPT = """You are a code audit expert. Analyze the following code and respond in **English**.

File path: {path}
Language: {language}

Code content:
```
{content}
```

Please respond (JSON format):
{{
    "purpose": "One-line description of what this file does (e.g. top-up page - display plans, handle payments)",
    "category": "Category (e.g. payment, auth, user, product, order, admin, util)",
    "keywords": ["Related keywords, e.g. topup, wallet, payment"],
    "apis": ["Called API paths, e.g. /api/wallet/topup"],
    "dependencies": ["Dependent composable/store/service, e.g. useWallet"],
    "ui_elements": ["Main UI elements, e.g. plan card, payment button"]
}}

Output JSON only, no other text.
"""

AUDIT_SYMBOL_PROMPT = """You are a code audit expert. Analyze the following function/class and respond in **English**.

File: {path}
Name: {name}
Type: {type}

Code:
```
{content}
```

Please respond (JSON format):
{{
    "purpose": "One-line description of what this {type} does",
    "params": ["Parameter descriptions"],
    "returns": "Return value description",
    "side_effects": ["Side effects, e.g. modify database, call API"],
    "keywords": ["Related keywords"]
}}

Output JSON only, no other text.
"""


class LLMAuditor:
    """
    LLM Auditor

    Uses LLM to understand code purpose and generate semantic descriptions
    """

    def __init__(self, provider: str = "openai", model: str = None):
        """
        Args:
            provider: "openai" or "ollama"
            model: Model name (defaults to gpt-4o-mini or llama3)
        """
        self.provider = provider
        self.model = model or self._default_model()
        self._client = None

    def _default_model(self) -> str:
        if self.provider == "openai":
            return "gpt-4o-mini"
        else:
            return "llama3"

    def _get_client(self):
        """Get LLM client"""
        if self._client:
            return self._client

        if self.provider == "openai":
            from openai import OpenAI
            self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        return self._client

    def audit_file(
        self,
        path: str,
        content: str,
        language: str = "unknown"
    ) -> dict:
        """
        Audit a single file

        Returns:
            {
                "path": str,
                "purpose": str,
                "category": str,
                "keywords": list,
                "apis": list,
                "dependencies": list,
                "ui_elements": list,
                "error": str or None
            }
        """
        # Truncate overly long content (max 4000 characters)
        if len(content) > 4000:
            content = content[:4000] + "\n... (truncated)"

        prompt = AUDIT_FILE_PROMPT.format(
            path=path,
            language=language,
            content=content
        )

        try:
            result = self._call_llm(prompt)
            parsed = json.loads(result)
            parsed["path"] = path
            parsed["error"] = None
            return parsed
        except Exception as e:
            logger.error(f"Audit failed for {path}: {e}")
            return {
                "path": path,
                "purpose": "",
                "category": "unknown",
                "keywords": [],
                "apis": [],
                "dependencies": [],
                "ui_elements": [],
                "error": str(e)
            }

    def audit_symbol(
        self,
        path: str,
        name: str,
        symbol_type: str,
        content: str
    ) -> dict:
        """
        Audit a single symbol (function/class/component)

        Returns:
            {
                "purpose": str,
                "params": list,
                "returns": str,
                "side_effects": list,
                "keywords": list,
                "error": str or None
            }
        """
        # Truncate overly long content
        if len(content) > 2000:
            content = content[:2000] + "\n... (truncated)"

        prompt = AUDIT_SYMBOL_PROMPT.format(
            path=path,
            name=name,
            type=symbol_type,
            content=content
        )

        try:
            result = self._call_llm(prompt)
            parsed = json.loads(result)
            parsed["error"] = None
            return parsed
        except Exception as e:
            logger.error(f"Audit failed for {name}: {e}")
            return {
                "purpose": "",
                "params": [],
                "returns": "",
                "side_effects": [],
                "keywords": [],
                "error": str(e)
            }

    def _call_llm(self, prompt: str) -> str:
        """Call LLM"""
        if self.provider == "openai":
            return self._call_openai(prompt)
        else:
            return self._call_ollama(prompt)

    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI"""
        client = self._get_client()
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a code auditor. Output valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()

    def _call_ollama(self, prompt: str) -> str:
        """Call Ollama"""
        import requests
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
            },
            timeout=60,
        )
        if response.status_code == 200:
            return response.json().get("response", "")
        raise Exception(f"Ollama error: {response.status_code}")


def audit_file(path: str, content: str, language: str = "unknown") -> dict:
    """Convenience function: audit a single file"""
    auditor = LLMAuditor()
    return auditor.audit_file(path, content, language)


def audit_project(
    project_path: Path,
    symbols: list[dict],
    output_file: Optional[Path] = None,
    max_files: int = 100,
    show_progress: bool = True
) -> dict:
    """
    Audit an entire project

    Args:
        project_path: Project root directory
        symbols: List of already-scanned symbols
        output_file: Output path for PROJECT_MAP.json
        max_files: Maximum number of files to audit
        show_progress: Whether to show progress

    Returns:
        {
            "project": str,
            "files": {path: audit_result},
            "categories": {category: [paths]},
            "api_map": {api: [paths]},
            "keyword_index": {keyword: [paths]}
        }
    """
    auditor = LLMAuditor()

    # Collect unique files
    files = {}
    for symbol in symbols:
        path = symbol.get("path")
        if path and path not in files:
            files[path] = symbol.get("content", "")

    # Limit count
    file_list = list(files.items())[:max_files]

    result = {
        "project": project_path.name,
        "files": {},
        "categories": {},
        "api_map": {},
        "keyword_index": {}
    }

    # Audit each file
    iterator = enumerate(file_list)
    if show_progress:
        try:
            from tqdm import tqdm
            iterator = tqdm(list(iterator), desc="Auditing files")
        except ImportError:
            pass

    for _i, (path, content) in iterator:
        # Infer language
        ext = Path(path).suffix
        lang_map = {".py": "python", ".vue": "vue", ".ts": "typescript", ".js": "javascript"}
        language = lang_map.get(ext, ext[1:] if ext else "unknown")

        # Read full content (if content is empty)
        if not content:
            full_path = project_path / path
            if full_path.exists():
                try:
                    content = full_path.read_text(encoding="utf-8")
                except Exception:
                    continue

        # Audit
        audit = auditor.audit_file(path, content, language)
        result["files"][path] = audit

        # Build indexes
        category = audit.get("category", "unknown")
        if category not in result["categories"]:
            result["categories"][category] = []
        result["categories"][category].append(path)

        for api in audit.get("apis", []):
            if api not in result["api_map"]:
                result["api_map"][api] = []
            result["api_map"][api].append(path)

        for keyword in audit.get("keywords", []):
            kw_lower = keyword.lower()
            if kw_lower not in result["keyword_index"]:
                result["keyword_index"][kw_lower] = []
            result["keyword_index"][kw_lower].append(path)

    # Write to file
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        logger.info(f"PROJECT_MAP saved to {output_file}")

    return result
