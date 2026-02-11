#!/usr/bin/env python3
"""
Demo: LLM audit + AI workflow

Complete flow:
1. Scan project
2. LLM audit each file (generate PROJECT_MAP)
3. User says 'I want to build feature XXX'
4. AI navigation: overview -> detail -> specifics -> impact analysis
"""

import sys
import os
from pathlib import Path

# Load environment variables
def load_dotenv():
    env_files = [
        Path(__file__).parent.parent / ".env",
    ]
    for env_file in env_files:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
            break

load_dotenv()

# Set up path
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))
os.chdir(src_path)

from auditor.llm_auditor import LLMAuditor, audit_project
from auditor.workflow import AIWorkflow


def demo_audit():
    """Demo: LLM audit"""
    print("\n" + "="*60)
    print("Demo: LLM Audit")
    print("="*60)

    auditor = LLMAuditor(provider="openai")

    # Audit a sample file
    sample_code = '''
<template>
  <div class="cart-page">
    <h1>Shopping Cart</h1>
    <div v-for="item in cartItems" :key="item.id">
      {{ item.name }} x {{ item.quantity }}
      <button @click="removeItem(item.id)">Remove</button>
    </div>
    <div class="total">Total: {{ total }}</div>
    <button @click="checkout" :disabled="loading">Checkout</button>
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

    print("\nAuditing sample file: Cart.vue")
    result = auditor.audit_file("src/pages/Cart.vue", sample_code, "vue")

    print(f"\nPurpose: {result.get('purpose')}")
    print(f"Category: {result.get('category')}")
    print(f"Keywords: {result.get('keywords')}")
    print(f"APIs: {result.get('apis')}")
    print(f"Dependencies: {result.get('dependencies')}")
    print(f"UI elements: {result.get('ui_elements')}")


def demo_workflow():
    """Demo: AI workflow"""
    print("\n" + "="*60)
    print("Demo: AI Workflow")
    print("="*60)

    # Build mock PROJECT_MAP
    project_map = {
        "files": {
            "src/pages/Cart.vue": {
                "purpose": "Shopping cart page - display products, modify quantities, checkout",
                "category": "cart",
                "keywords": ["shopping cart", "cart", "checkout", "payment"],
                "apis": ["/api/cart", "/api/checkout"],
                "dependencies": ["useCart", "usePayment"],
            },
            "src/pages/Product.vue": {
                "purpose": "Product detail page - display product info, add to cart",
                "category": "product",
                "keywords": ["product", "detail", "add to cart"],
                "apis": ["/api/product"],
                "dependencies": ["useCart", "useProduct"],
            },
            "src/composables/useCart.ts": {
                "purpose": "Cart composable - manage cart state and operations",
                "category": "cart",
                "keywords": ["shopping cart", "cart", "addToCart", "removeItem"],
                "apis": ["/api/cart"],
                "dependencies": [],
            },
        },
        "categories": {
            "cart": ["src/pages/Cart.vue", "src/composables/useCart.ts"],
            "product": ["src/pages/Product.vue"],
        },
        "keyword_index": {
            "shopping cart": ["src/pages/Cart.vue", "src/composables/useCart.ts"],
            "cart": ["src/pages/Cart.vue", "src/composables/useCart.ts"],
            "product": ["src/pages/Product.vue"],
            "detail": ["src/pages/Product.vue"],
        },
    }

    # Build mock index
    index = {
        "symbols": {
            "demo:src/composables/useCart.ts:function:addToCart": {
                "path": "src/composables/useCart.ts",
                "name": "addToCart",
                "type": "function",
                "start_line": 10,
                "end_line": 20,
                "summary": "Add a product to the shopping cart",
            },
            "demo:src/pages/Cart.vue:function:checkout": {
                "path": "src/pages/Cart.vue",
                "name": "checkout",
                "type": "function",
                "start_line": 30,
                "end_line": 40,
                "summary": "Handle the checkout flow",
            },
            "demo:src/pages/Product.vue:function:handleAddToCart": {
                "path": "src/pages/Product.vue",
                "name": "handleAddToCart",
                "type": "function",
                "start_line": 50,
                "end_line": 55,
                "summary": "Handle add-to-cart button on the product page",
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

    # Save temporary files
    import json
    import tempfile
    temp_dir = Path(tempfile.mkdtemp())
    project_map_path = temp_dir / "PROJECT_MAP.json"
    index_path = temp_dir / "index.json"
    project_map_path.write_text(json.dumps(project_map, ensure_ascii=False))
    index_path.write_text(json.dumps(index, ensure_ascii=False))

    # Build workflow
    workflow = AIWorkflow(project_map_path, index_path)

    # Simulate user query
    print("\n" + "-"*40)
    print("User: 'I want to modify the shopping cart feature'")
    print("-"*40)

    # Step 1: L0 search
    print("\n[Step 1] Overview search (L0)")
    l0 = workflow.search_l0("shopping cart")
    print(l0.suggestion)

    # Step 2: L1 file detail
    print("\n[Step 2] Detail search (L1)")
    l1 = workflow.search_l1("src/composables/useCart.ts")
    print(l1.suggestion)

    # Step 3: Impact analysis
    print("\n[Step 3] Impact analysis")
    impact = workflow.impact_analysis("demo:src/composables/useCart.ts:function:addToCart")
    print(f"Affected count: {impact['affected_count']}")
    print(f"Warning: {impact['warning']}")
    print(f"Suggestion: {impact['suggestion']}")
    print("\nAffected locations:")
    for a in impact["affected"]:
        print(f"  - {a['path']}: {a['name']} ({a['reason']})")

    # Cleanup
    import shutil
    shutil.rmtree(temp_dir)


def main():
    print("\n" + "="*60)
    print("Flyto Indexer - LLM Audit + AI Workflow Demo")
    print("="*60)

    if not os.getenv("OPENAI_API_KEY"):
        print("\nWARNING: OPENAI_API_KEY not set, skipping LLM audit demo")
    else:
        demo_audit()

    demo_workflow()

    print("\n" + "="*60)
    print("Demo complete!")
    print("="*60)


if __name__ == "__main__":
    main()
