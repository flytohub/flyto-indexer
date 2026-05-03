"""Layer-4 re-export resolution — verify cross-file chains."""
import sys
from pathlib import Path

import pytest

# Existing test suite imports from `src.<mod>` and bare-package mods —
# follow the same pattern by inserting both roots up front.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

from analyzer.call_sites_regex import (
    scan_project_call_sites,
    _build_py_reexport_map,
    _resolve_through_reexports,
)


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_reexport_map_captures_aliased_imports(tmp_path: Path) -> None:
    _write(tmp_path, "myapp/utils.py", """
from aiohttp import ZLibDecompressor as MyDecomp
from aiohttp import read
""")
    rmap = _build_py_reexport_map(tmp_path)
    assert rmap["myapp.utils.MyDecomp"] == "aiohttp.ZLibDecompressor"
    # `from X import Y` (no alias) is also recorded so chains can resolve.
    assert rmap["myapp.utils.read"] == "aiohttp.read"


def test_resolve_chain_rewrites_through_one_hop(tmp_path: Path) -> None:
    rmap = {"myapp.utils.MyDecomp": "aiohttp.ZLibDecompressor"}
    got = _resolve_through_reexports("myapp.utils.MyDecomp.decompress", rmap)
    assert got == "aiohttp.ZLibDecompressor.decompress"


def test_resolve_chain_handles_two_hops(tmp_path: Path) -> None:
    # Two-hop: A -> B -> origin
    rmap = {
        "myapp.api.MyDecomp": "myapp.utils.MyDecomp",
        "myapp.utils.MyDecomp": "aiohttp.ZLibDecompressor",
    }
    got = _resolve_through_reexports("myapp.api.MyDecomp.decompress", rmap)
    assert got == "aiohttp.ZLibDecompressor.decompress"


def test_resolve_chain_unaffected_when_no_match(tmp_path: Path) -> None:
    rmap = {"foo.bar": "baz.qux"}
    got = _resolve_through_reexports("hello.world", rmap)
    assert got == "hello.world"


def test_full_scan_records_both_wrapper_and_origin(tmp_path: Path) -> None:
    """End-to-end: handler.py calls myapp.utils.MyDecomp.decompress.
    Layer-4 must record BOTH the wrapper name and the resolved
    `aiohttp.ZLibDecompressor.decompress` so downstream Layer-3
    matching catches whichever the CVE keys on."""
    _write(tmp_path, "myapp/utils.py", """
from aiohttp import ZLibDecompressor as MyDecomp
""")
    _write(tmp_path, "myapp/handler.py", """
from myapp.utils import MyDecomp

def handle(req):
    return MyDecomp.decompress(req.body)
""")
    result = scan_project_call_sites(tmp_path)
    fc = result["function_calls"]

    # Wrapper form should still be retrievable for any rule that
    # wants to flag wrapper usage.
    myapp_calls = set(fc.get("myapp", []))
    assert any("MyDecomp" in c for c in myapp_calls), (
        f"expected wrapper trace under myapp.*, got {myapp_calls}"
    )

    # CRUCIAL: origin form must also be present so a CVE keyed on
    # `aiohttp.ZLibDecompressor.decompress` still hits this codebase.
    aiohttp_calls = set(fc.get("aiohttp", []))
    assert "aiohttp.ZLibDecompressor.decompress" in aiohttp_calls, (
        f"Layer-4 didn't expand wrapper to origin; got aiohttp={aiohttp_calls}"
    )


def test_full_scan_propagates_reexport_through_call_graph(tmp_path: Path) -> None:
    """Local call graph should record BOTH wrapper and origin so
    Layer-3 transitive closure can taint outer functions through
    re-export chains."""
    _write(tmp_path, "myapp/utils.py", """
from aiohttp import ZLibDecompressor as MyDecomp
""")
    _write(tmp_path, "myapp/handler.py", """
from myapp.utils import MyDecomp

def helper(req):
    return MyDecomp.decompress(req.body)
""")
    result = scan_project_call_sites(tmp_path)
    cg = result["local_call_graph"]
    helper_callees = set(cg.get("myapp/handler.py:helper", []))
    # Layer-4 must include the resolved origin form in the callee
    # list so the verify-side fixpoint can reach `aiohttp.ZLibDecompressor`.
    assert "aiohttp.ZLibDecompressor.decompress" in helper_callees, (
        f"call graph missing resolved origin; got {helper_callees}"
    )


def test_star_imports_dont_claim_resolution(tmp_path: Path) -> None:
    """`from pkg import *` — we don't know what came along, so we
    must NOT pretend to resolve. The wrapper-only entry stays."""
    rmap = {"myapp.utils.*": "aiohttp.*"}
    got = _resolve_through_reexports("myapp.utils.something.method", rmap)
    # Star resolution: leave alone (don't fabricate aiohttp.something.method).
    assert got == "myapp.utils.something.method"
