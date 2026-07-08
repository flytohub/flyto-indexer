"""Regression pin for the AI-agent security policy analyzer.

Each class has a positive fixture (must flag) and, where the guard idiom
applies, a negative fixture (guarded code must NOT flag). Keeps the rules from
silently breaking.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from analyzer.agent_policy import AgentPolicyAnalyzer  # noqa: E402


def _cats(tmp_path, name, code):
    (tmp_path / name).write_text(code, encoding="utf-8")
    findings = AgentPolicyAnalyzer(str(tmp_path)).analyze()
    return {f.category for f in findings}, findings


def test_ssrf_no_guard_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "m.py", (
        "import aiohttp\n"
        "async def mod(context):\n"
        "    params = context['params']\n"
        "    url = params.get('url')\n"
        "    async with aiohttp.ClientSession() as session:\n"
        "        await session.get(url)\n"
    ))
    assert "ssrf-no-guard" in cats


def test_ssrf_guarded_not_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "m.py", (
        "import aiohttp\n"
        "async def mod(context):\n"
        "    params = context['params']\n"
        "    url = params.get('url')\n"
        "    validate_url_with_env_config(url)\n"
        "    async with aiohttp.ClientSession() as session:\n"
        "        await session.get(url)\n"
    ))
    assert "ssrf-no-guard" not in cats


def test_unauth_route_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "r.py", (
        "async def run(body):\n"
        "    return {'ok': True}\n"
    ).replace("async def run", "@app.post('/run')\nasync def run"))
    assert "unauth-route" in cats


def test_auth_route_not_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "r.py", (
        "@app.post('/run')\n"
        "async def run(body, _=Depends(require_auth)):\n"
        "    return {'ok': True}\n"
    ))
    assert "unauth-route" not in cats


def test_dynamic_env_read_flagged_high(tmp_path):
    cats, findings = _cats(tmp_path, "e.py", (
        "import os\n"
        "def resolve(ref):\n"
        "    parts = ref.split('.')\n"
        "    return os.getenv(parts[1])\n"
    ))
    assert "dynamic-env-read" in cats
    assert any(f.category == "dynamic-env-read" and f.confidence == "high" for f in findings)


def test_bounded_env_selector_not_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "e.py", (
        "import os\n"
        "def key(provider):\n"
        "    env_vars = {'openai': 'OPENAI_API_KEY'}\n"
        "    return os.getenv(env_vars.get(provider))\n"
    ))
    assert "dynamic-env-read" not in cats


def test_file_write_no_guard_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "w.py", (
        "def write(context):\n"
        "    params = context['params']\n"
        "    p = params.get('output_path')\n"
        "    with open(p, 'wb') as f:\n"
        "        f.write(b'x')\n"
    ))
    assert "file-write-no-guard" in cats


def test_command_injection_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "c.py", (
        "import os\n"
        "def run(context):\n"
        "    cmd = context['params'].get('cmd')\n"
        "    os.system(cmd)\n"
    ))
    assert "command-injection" in cats


def test_unsafe_deserialization_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "d.py", (
        "import pickle\n"
        "def load(context):\n"
        "    blob = context['params'].get('blob')\n"
        "    return pickle.loads(blob)\n"
    ))
    assert "unsafe-deserialization" in cats


def test_key_to_endpoint_flagged(tmp_path):
    cats, findings = _cats(tmp_path, "llm.py", (
        "import os\n"
        "def chat(context):\n"
        "    base_url = context['params'].get('base_url')\n"
        "    api_key = os.getenv('OPENAI_API_KEY')\n"
        "    headers = {'Authorization': 'Bearer ' + api_key}\n"
        "    return base_url, headers\n"
    ))
    assert "key-to-endpoint" in cats


def test_findings_carry_cwe_and_rule_id(tmp_path):
    _, findings = _cats(tmp_path, "m.py", (
        "import aiohttp\n"
        "async def mod(context):\n"
        "    url = context['params'].get('url')\n"
        "    async with aiohttp.ClientSession() as session:\n"
        "        await session.get(url)\n"
    ))
    ssrf = [f for f in findings if f.category == "ssrf-no-guard"]
    assert ssrf and ssrf[0].cwe == "CWE-918"
    assert ssrf[0].rule_id == "agent/ssrf-no-guard"


def test_code_injection_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "x.py", (
        "def run(context):\n"
        "    expr = context['params'].get('expr')\n"
        "    return eval(expr)\n"
    ))
    assert "code-injection" in cats


def test_path_traversal_read_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "x.py", (
        "def read(context):\n"
        "    p = context['params'].get('path')\n"
        "    with open(p, 'r') as f:\n"
        "        return f.read()\n"
    ))
    assert "path-traversal-read" in cats


def test_ssti_flagged(tmp_path):
    cats, _ = _cats(tmp_path, "x.py", (
        "def render(context):\n"
        "    tpl = context['params'].get('tpl')\n"
        "    return render_template_string(tpl)\n"
    ))
    assert "ssti" in cats


def test_mcp_reachable_set_on_registered_handler(tmp_path):
    _, findings = _cats(tmp_path, "mod.py", (
        "import aiohttp\n"
        "@register_module(module_id='core.api.http_get')\n"
        "async def http_get(context):\n"
        "    url = context['params'].get('url')\n"
        "    async with aiohttp.ClientSession() as session:\n"
        "        await session.get(url)\n"
    ))
    ssrf = [f for f in findings if f.category == "ssrf-no-guard"]
    assert ssrf and ssrf[0].mcp_reachable is True


def test_exploitability_score_and_band(tmp_path):
    # reachable + high-signal class => high score => confirm band, no LLM needed
    _, findings = _cats(tmp_path, "mod.py", (
        "import aiohttp\n"
        "@register_module(module_id='core.api.http_get')\n"
        "async def http_get(context):\n"
        "    url = context['params'].get('url')\n"
        "    async with aiohttp.ClientSession() as session:\n"
        "        await session.get(url)\n"
    ))
    ssrf = [f for f in findings if f.category == "ssrf-no-guard"][0]
    assert 0 <= ssrf.exploitability <= 100
    assert ssrf.band == "confirm"          # reachable + high confidence
    assert "category:" in ssrf.score_factors and "reachable:True" in ssrf.score_factors


def test_low_signal_lands_in_review_or_drop(tmp_path):
    # non-reachable read from a caller path: low base + no reachability =>
    # NOT auto-confirm (goes to review/drop, i.e. escalated or dropped, not auto)
    _, findings = _cats(tmp_path, "helper.py", (
        "def _orphan(context):\n"
        "    p = context['params'].get('path')\n"
        "    with open(p, 'r') as f:\n"
        "        return f.read()\n"
    ))
    reads = [f for f in findings if f.category == "path-traversal-read"]
    assert reads and reads[0].band in ("review", "drop")
    assert reads[0].band != "confirm"


def test_not_mcp_reachable_for_unreferenced_helper(tmp_path):
    _, findings = _cats(tmp_path, "mod.py", (
        "import aiohttp\n"
        "async def _orphan(url):\n"
        "    async with aiohttp.ClientSession() as session:\n"
        "        await session.get(url)\n"
    ))
    ssrf = [f for f in findings if f.category == "ssrf-no-guard"]
    assert ssrf and ssrf[0].mcp_reachable is False
