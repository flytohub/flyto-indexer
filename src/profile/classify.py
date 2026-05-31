"""
Project type classification and pattern detection.
"""

import os
import re

from .constants import PATTERN_SIGNALS, SERVICE_SDKS


def classify_project_type(
    languages: dict,
    api_definitions: list,
    components: int,
    dep_names: set,
    patterns: list,
    entry_points: list,
    all_files: list,
) -> dict:
    """Classify project as frontend, backend, fullstack, library, cli, mobile, static, unknown.

    Returns {"type": "...", "sub_type": "..."}.
    """
    dep_names_lower = {d.lower().replace("-", "_").replace("/", "_") for d in dep_names if d}

    # Backend signals
    _server_basenames = {"server.py", "server.ts", "server.js", "server.go",
                         "worker.py", "worker.ts", "worker.js", "worker.go",
                         "app.py", "app.ts", "app.js", "app.go",
                         "main.go", "main.py"}
    has_server_entry = any(
        os.path.basename(ep).lower() in _server_basenames
        and not any(ep.lower().startswith(skip) for skip in ("test", "example", "benchmark"))
        for ep in entry_points
    )
    has_server_entry = has_server_entry or any(
        os.path.basename(ep).lower().startswith("main_") for ep in entry_points
    )
    web_framework_deps = {"fastapi", "flask", "django", "express", "koa", "hono", "gin",
                          "echo", "fiber", "actix_web", "rocket", "spring_boot",
                          "uvicorn", "gunicorn", "nest", "nestjs"}
    has_web_framework = bool(web_framework_deps & dep_names_lower)

    has_cmd_server = any("cmd/server" in ep.lower() or "cmd/worker" in ep.lower() for ep in entry_points)

    has_backend = (
        (len(api_definitions) > 0 and (has_server_entry or has_web_framework))
        or "api_server" in patterns
        or (has_server_entry and has_web_framework)
        or has_cmd_server
    )

    frontend_deps = {"react", "vue", "angular", "svelte", "next", "nuxt",
                     "react_dom", "vue_router", "svelte_kit", "solid_js",
                     "@angular_core", "angular_core"}
    has_frontend_deps = bool(frontend_deps & dep_names_lower)
    backend_langs = languages.get("Python", 0) + languages.get("Go", 0) + languages.get("Java", 0) + languages.get("Rust", 0)
    frontend_langs = languages.get("TypeScript", 0) + languages.get("JavaScript", 0) + languages.get("Vue", 0)
    frontend_is_dominant = frontend_langs > backend_langs
    if has_frontend_deps and backend_langs > frontend_langs * 3:
        has_frontend_deps = False
    has_frontend = has_frontend_deps or (components > 10 and frontend_is_dominant)

    ep_names_lower = [ep.lower() for ep in entry_points]
    has_cli_entry = any("cli" in ep or "__main__" in ep for ep in ep_names_lower)

    publishable_files = {"setup.py", "pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
    has_publishable = any(f in all_files for f in publishable_files)

    has_deployment = "containerization" in patterns or any(
        f.lower().startswith("dockerfile") or f.lower() == "docker-compose.yml"
        for f in all_files
    )
    is_library = (
        not has_frontend
        and has_publishable
        and not has_cmd_server
        and not (has_deployment and has_server_entry)
    )

    has_cli = has_cli_entry and not has_frontend and not has_backend and not is_library

    is_mobile = "Dart" in languages or "Swift" in languages or "Kotlin" in languages
    is_static = not has_backend and not has_frontend and "HTML" in languages

    go_dominant = languages.get("Go", 0) > max(frontend_langs, 1)
    go_mod_exists = "go.mod" in all_files
    has_go_cmd = any("cmd/" in f for f in all_files if f.endswith(".go"))
    if go_dominant and (go_mod_exists or has_go_cmd) and not has_frontend:
        has_backend = True

    # Primary classification
    if has_backend and has_frontend:
        project_type = "fullstack"
    elif has_backend and not is_library:
        project_type = "backend"
    elif has_frontend:
        project_type = "frontend"
    elif is_mobile:
        project_type = "mobile"
    elif is_library:
        project_type = "library"
    elif has_cli:
        project_type = "cli"
    elif is_static:
        project_type = "static"
    else:
        project_type = "unknown"

    # Sub-classification
    sub_type = ""
    if project_type == "backend":
        if "api_server" in patterns or "api_gateway" in patterns or has_cmd_server:
            sub_type = "api_server"
        elif any("worker" in ep for ep in ep_names_lower):
            sub_type = "worker"
        else:
            sub_type = "microservice"
    elif project_type == "frontend":
        ssr_deps = {"next", "nuxt", "svelte_kit", "remix", "gatsby", "astro"}
        component_lib_signals = (
            not any(f for f in all_files if f.endswith(("index.html", "app.vue", "App.vue", "App.tsx")))
            and components > 5
        )
        if ssr_deps & dep_names_lower:
            sub_type = "ssr"
        elif component_lib_signals:
            sub_type = "component_library"
        else:
            sub_type = "spa"
    elif project_type == "library":
        has_interfaces = any("interface" in f.lower() or "client" in f.lower()
                             for f in all_files if not f.startswith(".") and not f.startswith("test"))
        has_sdk_structure = any("sdk" in f.lower() for f in all_files) or has_interfaces
        if has_sdk_structure or len(api_definitions) > 0:
            sub_type = "sdk"
        else:
            framework_signals = {"middleware", "plugin", "hook", "provider", "adapter"}
            file_basenames = {os.path.basename(f).lower().split(".")[0] for f in all_files}
            if framework_signals & file_basenames:
                sub_type = "framework"
            else:
                sub_type = "utility"

    return {"type": project_type, "sub_type": sub_type}


def detect_patterns(all_files: list, dep_names: set,
                    index_data: dict | None = None) -> list:
    """Detect architectural patterns from file paths, dependency names, and index symbols."""
    detected = []

    dep_names_lower = {d.lower().replace("-", "_").replace("/", "_") for d in dep_names}

    for pattern_name, signals in PATTERN_SIGNALS.items():
        found = False

        for d in signals.get("dirs", []):
            for f in all_files:
                if f"/{d}/" in f"/{f}" or f.startswith(f"{d}/") or f"\\{d}\\" in f:
                    found = True
                    break
            if found:
                break

        if not found:
            for target_file in signals.get("files", []):
                for f in all_files:
                    if os.path.basename(f).lower() == target_file.lower():
                        found = True
                        break
                if found:
                    break

        if not found:
            for dep in signals.get("deps", []):
                dep_norm = dep.lower().replace("-", "_").replace("/", "_")
                if dep_norm in dep_names_lower:
                    found = True
                    break

        if found:
            detected.append(pattern_name)

    # --- Additional pattern detection ---
    if "auth_middleware" not in detected:
        auth_deps = {"firebase", "firebase_admin", "jwt", "pyjwt", "jose",
                     "oauth", "oauth2", "oauthlib", "authlib", "passport",
                     "jsonwebtoken", "next_auth", "nextauth"}
        if auth_deps & dep_names_lower:
            detected.append("auth_middleware")

    state_deps = {"react_query", "@tanstack_react_query", "tanstack_react_query",
                  "redux", "react_redux", "@reduxjs_toolkit", "reduxjs_toolkit",
                  "vuex", "pinia", "zustand", "mobx", "recoil", "jotai", "valtio"}
    if state_deps & dep_names_lower:
        detected.append("state_management")

    routing_deps = {"react_router", "react_router_dom", "vue_router",
                    "gorilla_mux", "@angular_router", "angular_router",
                    "next", "nuxt", "wouter", "reach_router"}
    if routing_deps & dep_names_lower:
        detected.append("routing")

    if "websocket" not in detected:
        realtime_deps = {"socket.io", "socket_io", "socket.io_client", "socket_io_client",
                         "ws", "actioncable", "action_cable", "pusher", "ably",
                         "centrifugo", "phoenix"}
        if realtime_deps & dep_names_lower:
            detected.append("realtime")

    if index_data:
        api_routes = index_data.get("api_routes", [])
        if len(api_routes) >= 5:
            detected.append("api_gateway")
        sym_counts = index_data.get("symbol_counts", {})
        if sym_counts.get("api", 0) > 0:
            detected.append("api_server")

    return sorted(set(detected))


def detect_services(deps_inventory: dict) -> list[dict]:
    """Match dependency names against known SDK map to detect services."""
    services = []
    seen_names = set()
    for dep in deps_inventory.get("dependencies", []):
        if not isinstance(dep, dict):
            continue
        raw_name = dep.get("name", "")
        ecosystem = dep.get("ecosystem", "")
        norm = re.sub(r"\[.*?\]", "", raw_name).strip().lower()

        matched_service = SERVICE_SDKS.get(raw_name)
        if not matched_service:
            matched_service = SERVICE_SDKS.get(norm)
        if not matched_service:
            matched_service = SERVICE_SDKS.get(norm.replace("_", "-"))
        if not matched_service and ecosystem == "go":
            best_key = ""
            for sdk_key, sdk_name in SERVICE_SDKS.items():
                if norm.startswith(sdk_key) and len(sdk_key) > len(best_key):
                    best_key = sdk_key
                    matched_service = sdk_name
        if matched_service and matched_service not in seen_names:
            seen_names.add(matched_service)
            services.append({
                "name": matched_service,
                "package": raw_name,
                "ecosystem": ecosystem,
            })
    return services
