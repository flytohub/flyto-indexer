import json
from pathlib import Path

from src import scip_adapter
from src.tools import references


def _varint(value):
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _field(number, value, wire=2):
    key = _varint((number << 3) | wire)
    if wire == 0:
        return key + _varint(value)
    return key + _varint(len(value)) + value


def _occurrence(symbol, line, roles=0):
    packed_range = b"".join(_varint(value) for value in (line, 0, 4))
    return (
        _field(1, packed_range)
        + _field(2, symbol.encode())
        + _field(3, roles, wire=0)
    )


def _document(path, occurrences):
    return _field(1, path.encode()) + b"".join(
        _field(2, occurrence) for occurrence in occurrences
    )


def _binary_index(symbol):
    definition = _document(
        "src/lib.py",
        [_occurrence(symbol, 0, scip_adapter.DEFINITION_ROLE)],
    )
    reference = _document("src/app.py", [_occurrence(symbol, 4)])
    return _field(2, definition) + _field(2, reference)


def _flyto_index(root):
    return {
        "root_path": str(root),
        "project_roots": {"proj": str(root)},
        "symbols": {
            "proj:src/lib.py:function:work": {
                "path": "src/lib.py",
                "name": "work",
                "start_line": 1,
                "end_line": 2,
            },
            "proj:src/app.py:function:run": {
                "path": "src/app.py",
                "name": "run",
                "start_line": 1,
                "end_line": 10,
            },
        },
        "dependencies": {},
        "reverse_index": {},
        "files": {"src/lib.py": {}, "src/app.py": {}},
    }


def test_loads_standard_binary_scip_without_runtime_dependency(tmp_path):
    artifact = tmp_path / "index.scip"
    symbol = "scip-python python demo 1.0 src/lib.py/work()."
    artifact.write_bytes(_binary_index(symbol))

    loaded = scip_adapter.load_scip_artifact(artifact)

    assert loaded["status"] == "loaded"
    assert loaded["format"] == "scip-protobuf"
    assert len(loaded["documents"]) == 2
    assert loaded["documents"][0]["occurrences"][0]["symbol"] == symbol


def test_loads_protobuf_json_and_camel_case_roles(tmp_path):
    artifact = tmp_path / "scip.json"
    artifact.write_text(json.dumps({
        "metadata": {"projectRoot": "file:///repo"},
        "documents": [{
            "relativePath": "src/lib.py",
            "language": "python",
            "occurrences": [{
                "range": [0, 0, 4],
                "symbol": "local work",
                "symbolRoles": 1,
            }],
        }],
    }))

    loaded = scip_adapter.load_scip_artifact(artifact)

    assert loaded["status"] == "loaded"
    assert loaded["metadata"]["project_root"] == "file:///repo"
    assert loaded["documents"][0]["occurrences"][0]["symbol_roles"] == 1


def test_find_references_prefers_scip_and_exposes_provenance(tmp_path, monkeypatch):
    symbol = "scip-python python demo 1.0 src/lib.py/work()."
    (tmp_path / "index.scip").write_bytes(_binary_index(symbol))
    index = _flyto_index(tmp_path)
    monkeypatch.setattr(references, "load_index", lambda: index)
    lsp_called = {"value": False}

    def lsp(*_args):
        lsp_called["value"] = True
        return []

    monkeypatch.setattr(references, "_enrich_with_lsp", lsp)

    result = references.find_references("proj:src/lib.py:function:work")

    assert result["semantic_evidence"]["selected"] == "scip"
    assert result["semantic_evidence"]["scip"]["status"] == "resolved"
    assert result["references"][0]["source"] == "scip"
    assert result["references"][0]["from_symbol"].endswith(":function:run")
    assert len(result["references"][0]["artifact_fingerprint"]) == 64
    assert lsp_called["value"] is False


def test_missing_scip_falls_back_to_lsp(tmp_path, monkeypatch):
    index = _flyto_index(tmp_path)
    monkeypatch.setattr(references, "load_index", lambda: index)
    monkeypatch.setattr(
        references,
        "_enrich_with_lsp",
        lambda *_args: [{
            "type": "usage",
            "from_symbol": "proj:src/app.py:function:run",
            "from_path": "src/app.py",
            "from_name": "run",
            "line": 5,
            "confidence": "high",
            "source": "lsp",
        }],
    )

    result = references.find_references("proj:src/lib.py:function:work")

    assert result["semantic_evidence"]["selected"] == "lsp"
    assert result["semantic_evidence"]["scip"]["status"] == "unavailable"
    assert result["references"][0]["source"] == "lsp"
