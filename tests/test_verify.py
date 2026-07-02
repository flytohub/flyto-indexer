"""Tests for the no-dependency verification gate."""

import os
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cli import cmd_verify, cmd_verify_baseline, cmd_verify_workspace
from src.verify import (
    _classify_product_surfaces,
    _check_cross_project_contract,
    _check_dynamic_validation_plan,
    _check_mcp_runtime_smoke,
    _check_product_loop_closure,
    _check_single_project_islands,
    _normalize_api_path,
    format_verification,
    format_workspace_verification,
    render_report,
    run_verification,
    run_workspace_verification,
)


def _write_project(root: Path, *, dependency: str = "", project_name: str = "demo"):
    (root / "src").mkdir(parents=True)
    deps = f'"{dependency}"' if dependency else ""
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f"name = \"{project_name}\"\n"
        "requires-python = \">=3.11\"\n"
        f"dependencies = [{deps}]\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "Use flyto-indexer. Run search and impact before edits. Run flyto-index verify before finishing.\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(".flyto-index/\n", encoding="utf-8")
    (root / "README.md").write_text(
        "# Demo\n\n## Installation\n\nRun setup.\n\n## Usage\n\nRun app.\n\n## API\n\nN/A.\n",
        encoding="utf-8",
    )
    (root / "src" / "auth.py").write_text(
        "def handle_auth(user):\n"
        "    return user == 'admin'\n",
        encoding="utf-8",
    )
    (root / "src" / "routes.py").write_text(
        "from auth import handle_auth\n\n"
        "def get_routes():\n"
        "    if handle_auth('admin'):\n"
        "        return ['/dashboard']\n"
        "    return ['/']\n",
        encoding="utf-8",
    )


def _write_indexer_ci(root: Path):
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "name: CI\n"
        "jobs:\n"
        "  lint:\n"
        "    steps:\n"
        "      - run: ruff check src/ && mypy src/\n"
        "  test:\n"
        "    steps:\n"
        "      - run: pytest tests/\n"
        "  verify:\n"
        "    steps:\n"
        "      - run: flyto-index verify . --full-scan --report /tmp/verify.sarif --report-format sarif --json\n"
        "  build:\n"
        "    steps:\n"
        "      - run: python -m build\n"
        "      - run: |\n"
        "          python - <<'PY'\n"
        "          runtime_requires = []\n"
        "          assert 'Requires-Dist:'\n"
        "          PY\n"
        "      - run: pip install --no-deps dist/*.whl && flyto-index --help\n",
        encoding="utf-8",
    )


def _write_indexer_package_config(root: Path):
    (root / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")
    (root / "NOTICE").write_text("Flyto\n", encoding="utf-8")
    (root / "config" / "rules").mkdir(parents=True)
    (root / "config" / "rules" / "demo.yaml").write_text("rules: []\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[build-system]\n"
        "requires = [\"hatchling\"]\n"
        "build-backend = \"hatchling.build\"\n\n"
        "[project]\n"
        "name = \"flyto-indexer\"\n"
        "requires-python = \">=3.11\"\n"
        "dependencies = []\n"
        "license-files = [\"LICENSE\", \"NOTICE\"]\n\n"
        "[project.scripts]\n"
        "flyto-index = \"flyto_indexer.cli:main\"\n\n"
        "[tool.hatch.build.targets.sdist]\n"
        "include = [\"/src\", \"/config\"]\n\n"
        "[tool.hatch.build.targets.wheel]\n"
        "packages = [\"src\"]\n\n"
        "[tool.hatch.build.targets.wheel.sources]\n"
        "\"src\" = \"flyto_indexer\"\n\n"
        "[tool.hatch.build.targets.wheel.force-include]\n"
        "\"config/rules\" = \"flyto_indexer/config/rules\"\n",
        encoding="utf-8",
    )


def _write_backend_index(root: Path, routes: list[tuple[str, str]]):
    root.mkdir(parents=True, exist_ok=True)
    (root / "go.mod").write_text("module backend\n", encoding="utf-8")
    index_dir = root / ".flyto-index"
    index_dir.mkdir(parents=True)
    symbols = {}
    for i, (method, path) in enumerate(routes):
        sid = f"backend:api/router.go:api:{method} {path}"
        symbols[sid] = {
            "project": "backend",
            "path": "api/router.go",
            "type": "api",
            "name": f"{method} {path}",
            "start_line": i + 1,
            "end_line": i + 1,
            "language": "go",
            "metadata": {"method": method, "path": path, "handler": "handler"},
        }
    (index_dir / "index.json").write_text(
        json.dumps({
            "project": "backend",
            "files": {},
            "symbols": symbols,
            "dependencies": [],
            "reverse_index": {},
        }),
        encoding="utf-8",
    )


def _write_frontend_client(root: Path, endpoint: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text('{"name":"frontend"}\n', encoding="utf-8")
    client = root / "src-next" / "lib" / "engine" / "client.ts"
    client.parent.mkdir(parents=True)
    client.write_text(
        "export function loadFootprint(orgId: string) {\n"
        f"  return request('GET', `{endpoint}`)\n"
        "}\n",
        encoding="utf-8",
    )


def _write_frontend_loop_index(root: Path, endpoint: str = "/api/v1/code/orgs/{id}/footprint/graph"):
    _write_frontend_client(root, endpoint)
    index_dir = root / ".flyto-index"
    index_dir.mkdir(parents=True, exist_ok=True)
    component_sid = "frontend:src-next/components/compounds/footprint/FootprintGraphView.tsx:component:FootprintGraphView"
    client_sid = "frontend:src-next/lib/engine/footprint.ts:file:footprint"
    (index_dir / "index.json").write_text(
        json.dumps({
            "project": "frontend",
            "files": {},
            "symbols": {
                component_sid: {
                    "project": "frontend",
                    "path": "src-next/components/compounds/footprint/FootprintGraphView.tsx",
                    "type": "component",
                    "name": "FootprintGraphView",
                    "language": "typescript",
                },
                client_sid: {
                    "project": "frontend",
                    "path": "src-next/lib/engine/footprint.ts",
                    "type": "file",
                    "name": "footprint",
                    "language": "typescript",
                },
            },
            "dependencies": {
                "footprint-call": {
                    "source": client_sid,
                    "target": endpoint,
                    "type": "api_calls",
                    "metadata": {"method": "GET", "url": endpoint},
                }
            },
            "reverse_index": {},
        }),
        encoding="utf-8",
    )


def _write_product_loop_evidence(root: Path):
    test_path = root / "src-next" / "components" / "compounds" / "footprint" / "FootprintGraphView.test.tsx"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("describe('footprint graph loop', () => {})\n", encoding="utf-8")
    recipe = root / "docs" / "platform-loops" / "footprint.yaml"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text("surface: footprint\nrecipe: footprint graph\n", encoding="utf-8")


def _write_dynamic_validation_project(
    root: Path,
    *,
    valid_route: bool = True,
    write_recipe: bool = True,
    guard_scripts: bool = True,
    machine_checkable_assertions: bool = True,
    assertion_kind: str = "route_renders_without_error",
):
    root.mkdir(parents=True, exist_ok=True)
    (root / "src-next").mkdir(parents=True, exist_ok=True)
    scripts = {
        "audit:loops": "node scripts/audit-platform-loops.mjs",
        "audit:navbar-smoke": "node scripts/audit-navbar-smoke-registry.mjs",
        "guard:branch": "npm run audit:loops && npm run audit:navbar-smoke",
        "compliance:ci": "node scripts/audit-compliance.mjs",
    } if guard_scripts else {}
    (root / "package.json").write_text(
        json.dumps({"name": "frontend", "scripts": scripts}),
        encoding="utf-8",
    )
    loop_dir = root / "docs" / "platform-loops"
    recipe_dir = loop_dir / "recipes"
    recipe_dir.mkdir(parents=True, exist_ok=True)
    route = {
        "id": "footprint.graph",
        "moduleId": "footprint.graph",
        "surface": "exposure",
        "pathTemplate": "/projects/:projectId/footprint?mode=engineer",
        "mode": "engineer",
        "scrollPolicy": "host",
        "expectedText": ["Footprint"],
    }
    if not valid_route:
        route.pop("expectedText")
        route["mode"] = "invalid"
    (loop_dir / "navbar-smoke-registry.json").write_text(
        json.dumps({"routes": [route]}),
        encoding="utf-8",
    )
    (loop_dir / "platform-loop-registry.json").write_text(
        json.dumps({
            "surfaces": [{
                "id": "exposure",
                "modules": ["footprint.graph"],
                "recipes": ["footprint-full-loop.yaml"],
            }],
        }),
        encoding="utf-8",
    )
    if write_recipe:
        assertions_block = (
            "assertions:\n"
            f"  - assert: {assertion_kind}\n"
            "    step: 1\n"
        ) if machine_checkable_assertions else (
            "assertions:\n"
            "  - footprint page renders without an error boundary\n"
        )
        (recipe_dir / "footprint-full-loop.yaml").write_text(
            "name: footprint-full-loop\n"
            "steps:\n"
            "  - module: browser.goto\n"
            "    params:\n"
            "      url: \"{{baseUrl}}/projects/{{projectId}}/footprint?mode=engineer\"\n"
            "  - module: browser.extract\n"
            "    params:\n"
            "      selector: main\n"
            + assertions_block,
            encoding="utf-8",
        )
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow_text = (
        "name: CI\n"
        "jobs:\n"
        "  verify:\n"
        "    steps:\n"
        "      - run: npm run audit:loops\n"
        "      - run: npm run audit:navbar-smoke\n"
        "      - run: npm run guard:branch\n"
        "      - run: npm run compliance:ci\n"
    ) if guard_scripts else (
        "name: CI\n"
        "jobs:\n"
        "  verify:\n"
        "    steps:\n"
        "      - run: npm test\n"
    )
    workflow.write_text(workflow_text, encoding="utf-8")


def _run_single_project_island_check(
    symbols: dict,
    dependencies: dict | None = None,
    reverse_index: dict | None = None,
    project_root: Path | None = None,
):
    index = SimpleNamespace(
        symbols=symbols,
        dependencies=dependencies or {},
        reverse_index=reverse_index or {},
    )
    checks = []

    def add_check(name, status, summary, *, metrics=None):
        checks.append({
            "name": name,
            "status": status,
            "summary": summary,
            "metrics": metrics or {},
        })

    engine = SimpleNamespace(index=index)
    if project_root is not None:
        engine.project_root = project_root
    _check_single_project_islands(engine, add_check)
    return {check["name"]: check for check in checks}


def test_run_verification_closes_core_loops(tmp_path):
    _write_project(tmp_path)

    result = run_verification(tmp_path, full_scan=True, query="handle_auth")

    assert result["pass"] is True
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["runtime_dependencies"]["status"] == "pass"
    assert checks["index_integrity"]["status"] == "pass"
    assert checks["context_loop"]["status"] == "pass"
    assert checks["impact_loop"]["status"] == "pass"
    assert checks["weak_scan_secrets"]["status"] == "pass"
    assert checks["agent_hygiene"]["status"] == "pass"


def test_run_verification_fails_runtime_dependencies(tmp_path):
    _write_project(tmp_path, dependency="requests>=2", project_name="flyto-indexer")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert result["pass"] is False
    assert checks["runtime_dependencies"]["status"] == "fail"


def test_run_verification_allows_dependencies_for_other_projects(tmp_path):
    _write_project(tmp_path, dependency="requests>=2", project_name="app")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert result["pass"] is True
    assert checks["runtime_dependencies"]["status"] == "pass"
    assert checks["runtime_dependencies"]["metrics"]["dependency_count"] == 1


def test_run_verification_includes_redacted_security_samples(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "src" / "settings.py").write_text(
        'AWS_ACCESS_KEY_ID = "AKIA1234567890ABCDEF"\n',
        encoding="utf-8",
    )

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    secrets = checks["weak_scan_secrets"]
    assert secrets["status"] == "fail"
    samples = secrets["metrics"]["samples"]
    assert samples
    assert samples[0]["file"] == "src/settings.py"
    assert samples[0]["masked_value"].endswith("***")
    assert "AKIA1234567890ABCDEF" not in json.dumps(samples)


def test_format_verification_includes_summary(tmp_path):
    _write_project(tmp_path)
    result = run_verification(tmp_path, full_scan=True)

    output = format_verification(result)

    assert "Flyto Verify" in output
    assert "Checks:" in output


def test_cmd_verify_json(tmp_path):
    _write_project(tmp_path)

    result = cmd_verify(Namespace(
        path=str(tmp_path),
        full_scan=True,
        query="handle_auth",
        symbol=None,
        strict=False,
        baseline=None,
        regression_only=False,
        save_baseline=None,
        policy=None,
        report=None,
        report_format="json",
        as_json=True,
    ))

    assert result["pass"] is True


def test_cmd_verify_saves_baseline(tmp_path):
    _write_project(tmp_path)
    baseline = tmp_path / "baseline.json"

    result = cmd_verify(Namespace(
        path=str(tmp_path),
        full_scan=True,
        query="handle_auth",
        symbol=None,
        strict=False,
        baseline=None,
        regression_only=False,
        save_baseline=str(baseline),
        policy=None,
        report=None,
        report_format="json",
        as_json=True,
    ))

    assert result["pass"] is True
    assert baseline.exists()


def test_verify_accepts_git_info_exclude_for_index_ignore(tmp_path):
    _write_project(tmp_path)
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    info_exclude = tmp_path / ".git" / "info" / "exclude"
    info_exclude.write_text(info_exclude.read_text() + "\n.flyto-index/\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("", encoding="utf-8")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["generated_index_ignore"]["status"] == "pass"


def test_verify_checks_index_ignore_without_agent_instructions(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "AGENTS.md").unlink()

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["agent_hygiene"]["status"] == "warn"
    assert checks["generated_index_ignore"]["status"] == "pass"


def test_regression_only_allows_existing_warning(tmp_path):
    _write_project(tmp_path)
    result = run_verification(tmp_path, full_scan=True)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "AGENTS.md").unlink()

    current = run_verification(
        tmp_path,
        full_scan=True,
        baseline_path=baseline,
        regression_only=True,
    )

    checks = {check["name"]: check for check in current["checks"]}
    assert current["pass"] is False
    assert checks["regression_gate"]["status"] == "fail"


def test_regression_only_ignores_unchanged_warning(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    result = run_verification(tmp_path, full_scan=True)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    current = run_verification(
        tmp_path,
        full_scan=True,
        baseline_path=baseline,
        regression_only=True,
    )

    checks = {check["name"]: check for check in current["checks"]}
    assert current["pass"] is True
    assert checks["agent_hygiene"]["status"] == "warn"
    assert checks["regression_gate"]["status"] == "pass"


def test_workspace_verification_aggregates_projects(tmp_path):
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    _write_project(project_a)
    _write_project(project_b)

    result = run_workspace_verification(
        tmp_path,
        project_paths=[project_a, project_b],
        full_scan=True,
    )

    assert result["pass"] is True
    assert result["summary"]["projects"] == 2
    assert len(result["projects"]) == 2
    assert "Flyto Workspace Verify" in format_workspace_verification(result)


def test_cross_project_contract_matches_frontend_to_backend(tmp_path):
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    _write_frontend_client(frontend, "/api/v1/code/orgs/${orgId}/footprint/path/${entityId}")
    _write_backend_index(backend, [("GET", "/api/v1/code/orgs/{id}/footprint/path/{entityId}")])
    checks = []

    _check_cross_project_contract([frontend, backend], checks)

    by_name = {check["name"]: check for check in checks}
    assert by_name["cross_project_contract"]["status"] == "pass"
    assert by_name["cross_project_contract"]["metrics"]["matched_calls"] == 1


def test_cross_project_contract_matches_company_scope_org_template(tmp_path):
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    _write_frontend_client(frontend, "/api/v1/code/orgs/${orgId}/footprint/company-scope")
    _write_backend_index(backend, [("GET", "/api/v1/code/orgs/{id}/footprint/company-scope")])
    checks = []

    _check_cross_project_contract([frontend, backend], checks)

    by_name = {check["name"]: check for check in checks}
    assert by_name["cross_project_contract"]["status"] == "pass"
    assert by_name["cross_project_contract"]["metrics"]["matched_calls"] == 1


def test_cross_project_contract_matches_router_template_variants(tmp_path):
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    _write_frontend_client(frontend, "/api/v1/code/orgs/${orgId}/footprint/repos/[repoId]/files/*")
    _write_backend_index(backend, [("GET", "/api/v1/code/orgs/{id}/footprint/repos/:repoId/files/{path}")])
    checks = []

    _check_cross_project_contract([frontend, backend], checks)

    by_name = {check["name"]: check for check in checks}
    assert by_name["cross_project_contract"]["status"] == "pass"
    assert by_name["cross_project_contract"]["metrics"]["matched_calls"] == 1


def test_normalize_api_path_handles_router_template_variants():
    assert _normalize_api_path("/api/v1/code/orgs/${orgId") == "/api/v1/code/orgs/{param}"
    assert _normalize_api_path("/api/v1/code/repos/[repoId]/files/*") == "/api/v1/code/repos/{param}/files/{param}"
    assert _normalize_api_path("/api/v1/code/repos/:repoId/files/{path}") == "/api/v1/code/repos/{param}/files/{param}"


def test_cross_project_contract_warns_for_unmatched_frontend_call(tmp_path):
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    _write_frontend_client(frontend, "/api/v1/code/orgs/${orgId}/footprint/path/${entityId}")
    _write_backend_index(backend, [("GET", "/api/v1/code/orgs/{id}/domains")])
    checks = []

    _check_cross_project_contract([frontend, backend], checks)

    by_name = {check["name"]: check for check in checks}
    assert by_name["cross_project_contract"]["status"] == "warn"
    assert by_name["cross_project_contract"]["metrics"]["unmatched_calls"] == 1


def test_cross_project_contract_strips_template_query_suffix(tmp_path):
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    _write_frontend_client(frontend, "/api/v1/code/orgs/${orgId}/footprint/actionable${qs}")
    _write_backend_index(backend, [("GET", "/api/v1/code/orgs/{id}/footprint/actionable")])
    checks = []

    _check_cross_project_contract([frontend, backend], checks)

    by_name = {check["name"]: check for check in checks}
    assert by_name["cross_project_contract"]["status"] == "pass"
    assert by_name["cross_project_contract"]["metrics"]["matched_calls"] == 1


def test_cross_project_contract_ignores_frontend_test_fixtures(tmp_path):
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    _write_frontend_client(frontend, "/api/v1/code/orgs/${orgId}/footprint/actionable")
    test_client = frontend / "src-next" / "lib" / "engine" / "__tests__" / "client.test.ts"
    test_client.parent.mkdir(parents=True)
    test_client.write_text("expect('/api/v1/code/orgs/org-1/missing-test-only')\n", encoding="utf-8")
    _write_backend_index(backend, [("GET", "/api/v1/code/orgs/{id}/footprint/actionable")])
    checks = []

    _check_cross_project_contract([frontend, backend], checks)

    by_name = {check["name"]: check for check in checks}
    assert by_name["cross_project_contract"]["status"] == "pass"
    assert by_name["cross_project_contract"]["metrics"]["unmatched_calls"] == 0


def test_product_loop_closure_passes_when_surface_has_full_loop(tmp_path):
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    _write_frontend_loop_index(frontend)
    _write_product_loop_evidence(frontend)
    _write_backend_index(backend, [("GET", "/api/v1/code/orgs/{id}/footprint/graph")])
    checks = []

    _check_product_loop_closure([frontend, backend], checks)

    by_name = {check["name"]: check for check in checks}
    check = by_name["product_loop_closure"]
    assert check["status"] == "pass"
    assert "exposure" in check["metrics"]["active_surfaces"]


def test_product_loop_closure_warns_when_frontend_call_has_no_backend_route(tmp_path):
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    _write_frontend_loop_index(frontend)
    _write_product_loop_evidence(frontend)
    _write_backend_index(backend, [])
    checks = []

    _check_product_loop_closure([frontend, backend], checks)

    by_name = {check["name"]: check for check in checks}
    check = by_name["product_loop_closure"]
    assert check["status"] == "warn"
    assert check["metrics"]["gaps"][0]["surface"] == "exposure"
    assert "frontend_calls_without_backend_route" in check["metrics"]["gaps"][0]["reasons"]


def test_product_loop_closure_warns_without_evidence_or_recipe(tmp_path):
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    _write_frontend_loop_index(frontend)
    _write_backend_index(backend, [("GET", "/api/v1/code/orgs/{id}/footprint/graph")])
    checks = []

    _check_product_loop_closure([frontend, backend], checks)

    by_name = {check["name"]: check for check in checks}
    check = by_name["product_loop_closure"]
    assert check["status"] == "warn"
    assert "missing_evidence_or_recipe" in check["metrics"]["gaps"][0]["reasons"]


def test_product_loop_surface_classifier_uses_token_boundaries():
    surfaces = _classify_product_surfaces("/api/v1/code/orgs/{id}/external-report")

    assert "exposure" in surfaces
    assert "assets" not in surfaces


def test_product_loop_surface_classifier_matches_darkweb_nav_ids():
    surfaces = _classify_product_surfaces(
        "src-next/types/modules.ts threat_actors data_leaks ioc_lookup sensor_map botshield"
    )

    assert "darkweb" in surfaces


def test_dynamic_validation_plan_passes_with_smoke_recipe_and_guards(tmp_path):
    frontend = tmp_path / "frontend"
    _write_dynamic_validation_project(frontend)
    checks = []

    _check_dynamic_validation_plan([frontend], checks)

    by_name = {check["name"]: check for check in checks}
    check = by_name["dynamic_validation_plan"]
    assert check["status"] == "pass"
    exposure = check["metrics"]["projects"][0]["surfaces"]["exposure"]
    assert exposure["browser_routes"] == ["footprint.graph"]
    assert exposure["browser_recipe_files"] == ["footprint-full-loop.yaml"]


def test_dynamic_validation_plan_warns_for_missing_recipe_and_invalid_route(tmp_path):
    frontend = tmp_path / "frontend"
    _write_dynamic_validation_project(
        frontend,
        valid_route=False,
        write_recipe=False,
        guard_scripts=False,
    )
    checks = []

    _check_dynamic_validation_plan([frontend], checks)

    by_name = {check["name"]: check for check in checks}
    check = by_name["dynamic_validation_plan"]
    assert check["status"] == "warn"
    exposure_gap = next(
        gap for gap in check["metrics"]["gaps"]
        if gap["surface"] == "exposure"
    )
    assert "missing_recipe_files" in exposure_gap["reasons"]
    assert "invalid_smoke_route_contract" in exposure_gap["reasons"]
    assert exposure_gap["missing_recipe_files"] == ["footprint-full-loop.yaml"]
    guard_gap = next(
        gap for gap in check["metrics"]["gaps"]
        if gap["surface"] == "workspace"
    )
    assert "missing_dynamic_validation_ci_guards" in guard_gap["reasons"]


def test_dynamic_validation_plan_warns_for_prose_assertions(tmp_path):
    frontend = tmp_path / "frontend"
    _write_dynamic_validation_project(frontend, machine_checkable_assertions=False)
    checks = []

    _check_dynamic_validation_plan([frontend], checks)

    by_name = {check["name"]: check for check in checks}
    check = by_name["dynamic_validation_plan"]
    assert check["status"] == "warn"
    exposure_gap = next(
        gap for gap in check["metrics"]["gaps"]
        if gap["surface"] == "exposure"
    )
    assert "recipes_without_machine_checkable_assertions" in exposure_gap["reasons"]
    assert exposure_gap["prose_assertion_recipes"] == ["footprint-full-loop.yaml"]


def test_dynamic_validation_plan_warns_for_unknown_assertion_kind(tmp_path):
    frontend = tmp_path / "frontend"
    _write_dynamic_validation_project(frontend, assertion_kind="looks_machine_checkable_but_is_not_known")
    checks = []

    _check_dynamic_validation_plan([frontend], checks)

    by_name = {check["name"]: check for check in checks}
    check = by_name["dynamic_validation_plan"]
    assert check["status"] == "warn"
    exposure_gap = next(
        gap for gap in check["metrics"]["gaps"]
        if gap["surface"] == "exposure"
    )
    assert "recipes_without_machine_checkable_assertions" in exposure_gap["reasons"]
    assert exposure_gap["prose_assertion_recipes"] == ["footprint-full-loop.yaml"]


def test_single_project_islands_warns_for_unwired_product_component():
    sid = "demo:src/components/footprint/FootprintPanel.tsx:component:FootprintPanel"
    checks = _run_single_project_island_check({
        sid: {
            "path": "src/components/footprint/FootprintPanel.tsx",
            "type": "component",
            "name": "FootprintPanel",
            "ref_count": 0,
        }
    })

    check = checks["single_project_islands"]
    assert check["status"] == "warn"
    assert check["metrics"]["island_count"] == 1
    assert check["metrics"]["island_samples"][0]["reason"] == "no_inbound_or_outbound_edges"


def test_single_project_islands_accepts_lazy_imported_component(tmp_path):
    component = tmp_path / "src" / "components" / "footprint" / "FootprintPanel.tsx"
    page = tmp_path / "src" / "pages" / "FootprintPage.tsx"
    component.parent.mkdir(parents=True)
    page.parent.mkdir(parents=True)
    component.write_text("export function FootprintPanel() { return null }\n", encoding="utf-8")
    page.write_text(
        "const FootprintPanel = lazy(() => "
        "import('../components/footprint/FootprintPanel').then(m => ({ default: m.FootprintPanel })))\n",
        encoding="utf-8",
    )
    sid = "demo:src/components/footprint/FootprintPanel.tsx:component:FootprintPanel"
    checks = _run_single_project_island_check(
        {
            sid: {
                "path": "src/components/footprint/FootprintPanel.tsx",
                "type": "component",
                "name": "FootprintPanel",
                "ref_count": 0,
            },
        },
        project_root=tmp_path,
    )

    check = checks["single_project_islands"]
    assert check["status"] == "pass"
    assert check["metrics"]["island_count"] == 0


def test_single_project_islands_ignores_plain_helpers():
    sid = "demo:src/utils/math.ts:function:sum"
    checks = _run_single_project_island_check({
        sid: {
            "path": "src/utils/math.ts",
            "type": "function",
            "name": "sum",
            "ref_count": 0,
        }
    })

    check = checks["single_project_islands"]
    assert check["status"] == "pass"
    assert check["metrics"]["island_count"] == 0


def test_single_project_islands_matches_in_repo_api_calls():
    api_sid = "demo:api/router.go:api:GET /api/v1/footprint"
    source_sid = "demo:src/lib/engine/client.ts:file:client"
    checks = _run_single_project_island_check(
        {
            api_sid: {
                "path": "api/router.go",
                "type": "api",
                "name": "GET /api/v1/footprint",
                "metadata": {"method": "GET", "path": "/api/v1/footprint"},
            },
            source_sid: {
                "path": "src/lib/engine/client.ts",
                "type": "file",
                "name": "client",
            },
        },
        {
            "call": {
                "source": source_sid,
                "target": "/api/v1/footprint",
                "type": "api_calls",
                "metadata": {"method": "GET", "url": "/api/v1/footprint"},
            }
        },
    )

    check = checks["single_project_islands"]
    assert check["status"] == "pass"
    assert check["metrics"]["api_definitions"] == 1
    assert check["metrics"]["api_calls"] == 1
    assert check["metrics"]["unmatched_api_calls"] == 0


def test_cmd_verify_workspace_json(tmp_path):
    project = tmp_path / "project"
    _write_project(project)

    result = cmd_verify_workspace(Namespace(
        path=str(tmp_path),
        projects=[str(project)],
        full_scan=True,
        strict=False,
        baseline_dir=None,
        regression_only=False,
        changed_only=False,
        base="",
        policy=None,
        report=None,
        report_format="json",
        as_json=True,
    ))

    assert result["pass"] is True
    assert result["summary"]["projects"] == 1


def test_verify_policy_budget_fails_named_warning(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    policy = tmp_path / ".flyto-rules.yaml"
    policy.write_text(
        "verify:\n"
        "  warn_as_fail: [agent_hygiene]\n",
        encoding="utf-8",
    )

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["agent_hygiene"]["status"] == "warn"
    assert checks["policy_budget"]["status"] == "fail"
    assert result["pass"] is False


def test_verify_policy_budget_allows_named_warning(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    policy = tmp_path / ".flyto-rules.yaml"
    policy.write_text(
        "verify:\n"
        "  warn_as_fail: ['*']\n"
        "  allow_warn:\n"
        "    - agent_hygiene\n"
        "    - docs_coverage\n"
        "    - ci_closed_loop\n",
        encoding="utf-8",
    )

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["agent_hygiene"]["status"] == "warn"
    assert checks["policy_budget"]["status"] == "pass"
    assert result["pass"] is True


def test_no_external_runtime_and_ci_closed_loop_pass_for_indexer(tmp_path):
    _write_project(tmp_path, project_name="flyto-indexer")
    _write_indexer_ci(tmp_path)

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["no_external_runtime"]["status"] == "pass"
    assert checks["ci_closed_loop"]["status"] == "pass"


def test_package_integrity_passes_for_indexer_config(tmp_path):
    _write_project(tmp_path, project_name="flyto-indexer")
    _write_indexer_ci(tmp_path)
    _write_indexer_package_config(tmp_path)

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["package_integrity"]["status"] == "pass"


def test_baseline_integrity_fails_wrong_project(tmp_path):
    _write_project(tmp_path)
    baseline_result = run_verification(tmp_path, full_scan=True)
    baseline_result["project"] = "other-project"
    baseline_result["metadata"]["project"] = "other-project"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(baseline_result, ensure_ascii=False), encoding="utf-8")

    result = run_verification(tmp_path, full_scan=True, baseline_path=baseline, regression_only=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["baseline_integrity"]["status"] == "fail"
    assert result["pass"] is False


def test_mcp_runtime_smoke_passes_for_repo():
    root = Path(__file__).parent.parent
    checks = []

    def add_check(name, status, summary, *, metrics=None):
        checks.append({"name": name, "status": status, "summary": summary, "metrics": metrics or {}})

    _check_mcp_runtime_smoke(root, add_check)

    by_name = {check["name"]: check for check in checks}
    assert by_name["mcp_runtime_smoke"]["status"] == "pass"


def test_ci_closed_loop_warns_without_verify(tmp_path):
    _write_project(tmp_path, project_name="flyto-indexer")
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "name: CI\njobs:\n  test:\n    steps:\n      - run: pytest tests/\n",
        encoding="utf-8",
    )

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["ci_closed_loop"]["status"] == "warn"
    assert "verify" in checks["ci_closed_loop"]["metrics"]["missing"]


def test_ci_closed_loop_accepts_npm_verify_script_expansion(tmp_path):
    _write_project(tmp_path, project_name="flyto-docs")
    (tmp_path / "package.json").write_text(
        json.dumps({
            "scripts": {
                "test": "node scripts/audit-docs-public.mjs",
                "lint": "node scripts/audit-docs-public.mjs",
                "build": "vitepress build",
                "verify": "npm run test && npm run lint && npm run build",
            }
        }),
        encoding="utf-8",
    )
    workflow = tmp_path / ".github" / "workflows" / "deploy.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "name: Deploy\njobs:\n  build:\n    steps:\n      - run: npm run verify\n",
        encoding="utf-8",
    )

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["ci_closed_loop"]["status"] == "pass"
    assert checks["ci_closed_loop"]["metrics"]["missing"] == []


def test_change_hygiene_warns_on_high_risk_paths(tmp_path):
    _write_project(tmp_path)
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )
    (tmp_path / ".env.production").write_text("TOKEN=\n", encoding="utf-8")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["change_hygiene"]["status"] == "warn"
    assert ".env.production" in checks["change_hygiene"]["metrics"]["high_risk"]


def test_change_hygiene_allows_env_example(tmp_path):
    _write_project(tmp_path)
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )
    (tmp_path / ".env.example").write_text("PUBLIC_URL=https://example.com\n", encoding="utf-8")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["change_hygiene"]["status"] == "pass"
    assert ".env.example" not in checks["change_hygiene"]["metrics"]["high_risk"]


def test_change_hygiene_fails_generated_dist_by_default(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "bundle.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )
    (tmp_path / "dist" / "bundle.json").write_text('{"changed": true}\n', encoding="utf-8")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["change_hygiene"]["status"] == "fail"
    assert checks["change_hygiene"]["metrics"]["generated"] == ["dist/bundle.json"]


def test_change_hygiene_allows_policy_owned_generated_dist(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "bundle.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".flyto-rules.yaml").write_text(
        "verify:\n"
        "  allow_generated_changes:\n"
        "    - dist/**\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )
    (tmp_path / "dist" / "bundle.json").write_text('{"changed": true}\n', encoding="utf-8")

    result = run_verification(tmp_path, full_scan=True)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["change_hygiene"]["status"] == "pass"
    assert checks["change_hygiene"]["metrics"]["generated"] == []
    assert checks["change_hygiene"]["metrics"]["allowed_generated"] == ["dist/bundle.json"]


def test_render_report_formats(tmp_path):
    _write_project(tmp_path)
    result = run_verification(tmp_path, full_scan=True)

    markdown = render_report(result, "markdown")
    junit = render_report(result, "junit")
    sarif = render_report(result, "sarif")

    assert "# Flyto Verify" in markdown
    assert "<testsuite" in junit
    assert '"version": "2.1.0"' in sarif


def test_cmd_verify_writes_report(tmp_path):
    _write_project(tmp_path)
    report = tmp_path / "verify.md"

    result = cmd_verify(Namespace(
        path=str(tmp_path),
        full_scan=True,
        query="handle_auth",
        symbol=None,
        strict=False,
        baseline=None,
        regression_only=False,
        save_baseline=None,
        policy=None,
        report=str(report),
        report_format="markdown",
        as_json=True,
    ))

    assert result["pass"] is True
    assert "# Flyto Verify" in report.read_text(encoding="utf-8")


def test_workspace_changed_only_skips_clean_git_project(tmp_path):
    project = tmp_path / "project"
    _write_project(project)
    subprocess.run(["git", "init", str(project)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(project), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )

    result = run_workspace_verification(
        tmp_path,
        project_paths=[project],
        full_scan=True,
        changed_only=True,
    )

    assert result["summary"]["projects"] == 0
    assert result["summary"]["skipped"] == 1


def test_workspace_changed_only_detects_untracked_files(tmp_path):
    project = tmp_path / "project"
    _write_project(project)
    subprocess.run(["git", "init", str(project)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(project), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )
    (project / "new_module.py").write_text("def handle_new_event():\n    return True\n", encoding="utf-8")

    result = run_workspace_verification(
        tmp_path,
        project_paths=[project],
        full_scan=True,
        changed_only=True,
    )

    assert result["summary"]["projects"] == 1
    assert result["summary"]["skipped"] == 0
    assert result["projects"][0]["project"] == "project"


def test_cmd_verify_baseline_create_and_compare(tmp_path):
    _write_project(tmp_path)
    baseline_dir = tmp_path / "baselines"

    created = cmd_verify_baseline(Namespace(
        action="create",
        path=str(tmp_path),
        output_dir=str(baseline_dir),
        baseline=None,
        full_scan=True,
        as_json=True,
    ))
    compared = cmd_verify_baseline(Namespace(
        action="compare",
        path=str(tmp_path),
        output_dir=str(baseline_dir),
        baseline=None,
        full_scan=True,
        as_json=True,
    ))

    assert created["ok"] is True
    assert compared["pass"] is True
