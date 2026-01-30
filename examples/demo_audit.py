#!/usr/bin/env python3
"""
Demo: LLM 審計 + AI 工作流程

完整流程：
1. 掃描專案
2. LLM 審計每個檔案（生成 PROJECT_MAP）
3. 用戶說「我要做 XXX 功能」
4. AI 導航：大向 → 中向 → 細項 → 影響分析
"""

import sys
import os
from pathlib import Path

# 載入環境變數
def load_dotenv():
    env_files = [
        Path(__file__).parent.parent / ".env",
        Path("/Library/其他專案/flytohub/flyto-pro/.env"),
    ]
    for env_file in env_files:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
            break

load_dotenv()

# 設定路徑
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))
os.chdir(src_path)

from auditor.llm_auditor import LLMAuditor, audit_project
from auditor.workflow import AIWorkflow


def demo_audit():
    """Demo: LLM 審計"""
    print("\n" + "="*60)
    print("Demo: LLM 審計")
    print("="*60)

    auditor = LLMAuditor(provider="openai")

    # 審計一個範例檔案
    sample_code = '''
<template>
  <div class="cart-page">
    <h1>購物車</h1>
    <div v-for="item in cartItems" :key="item.id">
      {{ item.name }} x {{ item.quantity }}
      <button @click="removeItem(item.id)">移除</button>
    </div>
    <div class="total">總計: {{ total }}</div>
    <button @click="checkout" :disabled="loading">結帳</button>
  </div>
</template>

<script setup>
import { useCart } from '@/composables/useCart'
import { usePayment } from '@/composables/usePayment'

const { cartItems, total, removeItem, clearCart } = useCart()
const { processPayment, loading } = usePayment()

async function checkout() {
  const result = await processPayment(cartItems.value, total.value)
  if (result.ok) {
    clearCart()
    router.push('/order/success')
  }
}
</script>
'''

    print("\n審計範例檔案: Cart.vue")
    result = auditor.audit_file("src/pages/Cart.vue", sample_code, "vue")

    print(f"\n用途: {result.get('purpose')}")
    print(f"分類: {result.get('category')}")
    print(f"關鍵字: {result.get('keywords')}")
    print(f"APIs: {result.get('apis')}")
    print(f"依賴: {result.get('dependencies')}")
    print(f"UI元素: {result.get('ui_elements')}")


def demo_workflow():
    """Demo: AI 工作流程"""
    print("\n" + "="*60)
    print("Demo: AI 工作流程")
    print("="*60)

    # 建立模擬的 PROJECT_MAP
    project_map = {
        "files": {
            "src/pages/Cart.vue": {
                "purpose": "購物車頁面 - 顯示商品、修改數量、結帳",
                "category": "cart",
                "keywords": ["購物車", "cart", "結帳", "checkout"],
                "apis": ["/api/cart", "/api/checkout"],
                "dependencies": ["useCart", "usePayment"],
            },
            "src/pages/Product.vue": {
                "purpose": "商品詳情頁 - 顯示商品資訊、加入購物車",
                "category": "product",
                "keywords": ["商品", "product", "加入購物車"],
                "apis": ["/api/product"],
                "dependencies": ["useCart", "useProduct"],
            },
            "src/composables/useCart.ts": {
                "purpose": "購物車 composable - 管理購物車狀態和操作",
                "category": "cart",
                "keywords": ["購物車", "cart", "addToCart", "removeItem"],
                "apis": ["/api/cart"],
                "dependencies": [],
            },
        },
        "categories": {
            "cart": ["src/pages/Cart.vue", "src/composables/useCart.ts"],
            "product": ["src/pages/Product.vue"],
        },
        "keyword_index": {
            "購物車": ["src/pages/Cart.vue", "src/composables/useCart.ts"],
            "cart": ["src/pages/Cart.vue", "src/composables/useCart.ts"],
            "商品": ["src/pages/Product.vue"],
            "product": ["src/pages/Product.vue"],
        },
    }

    # 建立模擬的 index
    index = {
        "symbols": {
            "demo:src/composables/useCart.ts:function:addToCart": {
                "path": "src/composables/useCart.ts",
                "name": "addToCart",
                "type": "function",
                "start_line": 10,
                "end_line": 20,
                "summary": "將商品加入購物車",
            },
            "demo:src/pages/Cart.vue:function:checkout": {
                "path": "src/pages/Cart.vue",
                "name": "checkout",
                "type": "function",
                "start_line": 30,
                "end_line": 40,
                "summary": "處理結帳流程",
            },
            "demo:src/pages/Product.vue:function:handleAddToCart": {
                "path": "src/pages/Product.vue",
                "name": "handleAddToCart",
                "type": "function",
                "start_line": 50,
                "end_line": 55,
                "summary": "商品頁加入購物車按鈕處理",
            },
        },
        "dependencies": {
            "dep1": {
                "source": "demo:src/pages/Cart.vue:function:checkout",
                "target": "demo:src/composables/useCart.ts:function:addToCart",
                "type": "calls",
            },
            "dep2": {
                "source": "demo:src/pages/Product.vue:function:handleAddToCart",
                "target": "demo:src/composables/useCart.ts:function:addToCart",
                "type": "calls",
            },
        },
    }

    # 保存臨時檔案
    import json
    import tempfile
    temp_dir = Path(tempfile.mkdtemp())
    project_map_path = temp_dir / "PROJECT_MAP.json"
    index_path = temp_dir / "index.json"
    project_map_path.write_text(json.dumps(project_map, ensure_ascii=False))
    index_path.write_text(json.dumps(index, ensure_ascii=False))

    # 建立 workflow
    workflow = AIWorkflow(project_map_path, index_path)

    # 模擬用戶查詢
    print("\n" + "-"*40)
    print("用戶: 「我要修改購物車功能」")
    print("-"*40)

    # Step 1: L0 搜尋
    print("\n[Step 1] 大向搜尋 (L0)")
    l0 = workflow.search_l0("購物車")
    print(l0.suggestion)

    # Step 2: L1 查看檔案
    print("\n[Step 2] 中向搜尋 (L1)")
    l1 = workflow.search_l1("src/composables/useCart.ts")
    print(l1.suggestion)

    # Step 3: 影響分析
    print("\n[Step 3] 影響分析")
    impact = workflow.impact_analysis("demo:src/composables/useCart.ts:function:addToCart")
    print(f"影響數量: {impact['affected_count']}")
    print(f"警告: {impact['warning']}")
    print(f"建議: {impact['suggestion']}")
    print("\n受影響的地方:")
    for a in impact["affected"]:
        print(f"  - {a['path']}: {a['name']} ({a['reason']})")

    # 清理
    import shutil
    shutil.rmtree(temp_dir)


def main():
    print("\n" + "="*60)
    print("Flyto Indexer - LLM 審計 + AI 工作流程 Demo")
    print("="*60)

    if not os.getenv("OPENAI_API_KEY"):
        print("\n⚠️ OPENAI_API_KEY 未設定，跳過 LLM 審計 demo")
    else:
        demo_audit()

    demo_workflow()

    print("\n" + "="*60)
    print("Demo 完成！")
    print("="*60)


if __name__ == "__main__":
    main()
