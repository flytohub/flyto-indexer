# Dockerfile for flyto-indexer worker image.
#
# Ships the MCP server entrypoint plus the Semgrep CE and Checkov binaries that
# the scanner adapters (semgrep_adapter.py, checkov_adapter.py) shell out to.
# NOTICE is copied into /app so downstream images inherit license attribution
# per CFO determination in FLY-37.
#
# Build:
#   docker build -t flyto-indexer:$(git rev-parse --short HEAD) .
#
# Run (MCP server over stdio):
#   docker run --rm -i flyto-indexer
#
# Run a one-shot scan (subprocess adapter target):
#   docker run --rm -v "$PWD:/repo" flyto-indexer flyto-index scan /repo

# ---------- build stage ----------
FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /src

# Build deps only; runtime image won't carry these.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src/ ./src/
# config/rules is force-included into the wheel (see pyproject
# [tool.hatch.build.targets.wheel.force-include]); the wheel build fails
# without it present in the build context.
COPY config/ ./config/

RUN pip install --upgrade pip build \
    && python -m build --wheel --outdir /wheels .

# ---------- runtime stage ----------
FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLYTO_INDEXER_HOME=/app

# Apply all currently available Debian fixes before adding the minimal runtime
# dependencies. The upstream slim image can lag a fixable package revision even
# when its tag digest is current; Trivy remains the final HIGH/CRITICAL gate.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        tini \
    && dpkg --compare-versions \
        "$(dpkg-query -W -f='${Version}' libexpat1)" \
        ge "2.8.2-1~deb13u1" \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# License attribution for the bundled third-party scanners (FLY-37).
COPY NOTICE LICENSE /app/

COPY --from=build /wheels/*.whl /tmp/

# Pin semgrep + checkov versions explicitly so the image is reproducible across
# CI rebuilds. Update the pins when W2-BE-ADAPTERS tests are rerun against new
# releases. Semgrep 1.170 declares MCP 1.23.3, which is affected by
# CVE-2026-59950. Checkov 3.3.10 is the first 3.3.x release compatible with the
# aiohttp 3.14.3 fix for CVE-2026-69244. Checkov's resolved dependency set can
# also downgrade msgpack and setuptools to vulnerable releases. Re-apply all
# tested security overrides after the complete dependency solve; the import and
# CLI smoke checks below guard the intentionally overridden Semgrep MCP
# dependency.
RUN pip install --upgrade pip \
    && pip install \
        /tmp/*.whl \
        "semgrep==1.170.0" \
        "checkov==3.3.10" \
        "aiohttp==3.14.3" \
        "protobuf>=6.33.5,<7" \
    && pip install --upgrade \
        "mcp==1.29.0" \
        "msgpack==1.2.1" \
        "setuptools==83.0.0" \
    && rm -f /tmp/*.whl \
    && python -c "from importlib.metadata import version; expected={'aiohttp': '3.14.3', 'checkov': '3.3.10', 'mcp': '1.29.0', 'msgpack': '1.2.1', 'setuptools': '83.0.0'}; actual={name: version(name) for name in expected}; assert actual == expected, actual" \
    && python -c "from mcp.server.fastmcp import FastMCP; assert FastMCP" \
    && semgrep --version \
    && checkov --version

# Non-root runtime.
RUN useradd --system --create-home --shell /usr/sbin/nologin indexer
USER indexer
WORKDIR /home/indexer

ENTRYPOINT ["/usr/bin/tini", "--", "flyto-index"]
CMD ["--help"]
