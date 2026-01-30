"""
LLM Auditor - 用 LLM 理解代碼用途

核心功能：
1. audit_file() - 審計單個檔案，生成用途描述
2. audit_project() - 審計整個專案，生成 PROJECT_MAP
3. 增量審計 - 只審計變化的檔案

輸出格式（存入向量庫）：
{
    "path": "src/pages/TopUp.vue",
    "purpose": "儲值頁面 - 顯示方案列表、處理付款、跳轉成功頁",
    "category": "payment",
    "keywords": ["儲值", "付款", "wallet", "topup"],
    "apis": ["/api/wallet/topup", "/api/wallet/plans"],
    "dependencies": ["useWallet", "usePayment"],
    "ui_elements": ["方案卡片", "付款按鈕", "loading 狀態"]
}
"""

import os
import json
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# 審計 prompt
AUDIT_FILE_PROMPT = """你是代碼審計專家。分析以下代碼，用**繁體中文**回答。

檔案路徑：{path}
語言：{language}

代碼內容：
```
{content}
```

請回答（JSON 格式）：
{{
    "purpose": "一句話描述這個檔案在做什麼（例如：儲值頁面 - 顯示方案、處理付款）",
    "category": "分類（例如：payment, auth, user, product, order, admin, util）",
    "keywords": ["相關關鍵字，中英文都要，例如：儲值, 付款, topup, wallet"],
    "apis": ["呼叫的 API 路徑，例如：/api/wallet/topup"],
    "dependencies": ["依賴的 composable/store/service，例如：useWallet"],
    "ui_elements": ["主要 UI 元素，例如：方案卡片, 付款按鈕"]
}}

只輸出 JSON，不要其他文字。
"""

AUDIT_SYMBOL_PROMPT = """你是代碼審計專家。分析以下函數/類，用**繁體中文**回答。

檔案：{path}
名稱：{name}
類型：{type}

代碼：
```
{content}
```

請回答（JSON 格式）：
{{
    "purpose": "一句話描述這個 {type} 在做什麼",
    "params": ["參數說明"],
    "returns": "返回值說明",
    "side_effects": ["副作用，例如：修改資料庫、呼叫 API"],
    "keywords": ["相關關鍵字"]
}}

只輸出 JSON，不要其他文字。
"""


class LLMAuditor:
    """
    LLM 審計器

    使用 LLM 理解代碼用途，生成語義描述
    """

    def __init__(self, provider: str = "openai", model: str = None):
        """
        Args:
            provider: "openai" 或 "ollama"
            model: 模型名稱（預設 gpt-4o-mini 或 llama3）
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
        """取得 LLM client"""
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
        審計單個檔案

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
        # 截斷過長的內容（最多 4000 字符）
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
        審計單個 symbol（function/class/component）

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
        # 截斷過長的內容
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
        """呼叫 LLM"""
        if self.provider == "openai":
            return self._call_openai(prompt)
        else:
            return self._call_ollama(prompt)

    def _call_openai(self, prompt: str) -> str:
        """呼叫 OpenAI"""
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
        """呼叫 Ollama"""
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
    """便捷函數：審計單個檔案"""
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
    審計整個專案

    Args:
        project_path: 專案根目錄
        symbols: 已掃描的 symbols 列表
        output_file: 輸出 PROJECT_MAP.json 路徑
        max_files: 最多審計幾個檔案
        show_progress: 是否顯示進度

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

    # 收集唯一檔案
    files = {}
    for symbol in symbols:
        path = symbol.get("path")
        if path and path not in files:
            files[path] = symbol.get("content", "")

    # 限制數量
    file_list = list(files.items())[:max_files]

    result = {
        "project": project_path.name,
        "files": {},
        "categories": {},
        "api_map": {},
        "keyword_index": {}
    }

    # 審計每個檔案
    iterator = enumerate(file_list)
    if show_progress:
        try:
            from tqdm import tqdm
            iterator = tqdm(list(iterator), desc="Auditing files")
        except ImportError:
            pass

    for i, (path, content) in iterator:
        # 推斷語言
        ext = Path(path).suffix
        lang_map = {".py": "python", ".vue": "vue", ".ts": "typescript", ".js": "javascript"}
        language = lang_map.get(ext, ext[1:] if ext else "unknown")

        # 讀取完整內容（如果 content 為空）
        if not content:
            full_path = project_path / path
            if full_path.exists():
                try:
                    content = full_path.read_text(encoding="utf-8")
                except Exception:
                    continue

        # 審計
        audit = auditor.audit_file(path, content, language)
        result["files"][path] = audit

        # 建立索引
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

    # 輸出到檔案
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        logger.info(f"PROJECT_MAP saved to {output_file}")

    return result
