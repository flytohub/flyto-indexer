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

RUN pip install --upgrade pip build \
    && python -m build --wheel --outdir /wheels .

# ---------- runtime stage ----------
FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLYTO_INDEXER_HOME=/app

# Minimal runtime deps for semgrep (requires libc + git for repo-aware rules).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# License attribution for the bundled third-party scanners (FLY-37).
COPY NOTICE LICENSE /app/

COPY --from=build /wheels/*.whl /tmp/

# Pin semgrep + checkov versions explicitly so the image is reproducible across
# CI rebuilds. Update the pins when W2-BE-ADAPTERS tests are rerun against new
# releases.
RUN pip install --upgrade pip "setuptools<80" \
    && pip install \
        /tmp/*.whl \
        "semgrep==1.95.0" \
        "checkov==3.2.334" \
    && rm -f /tmp/*.whl \
    && semgrep --version \
    && checkov --version

# Non-root runtime.
RUN useradd --system --create-home --shell /usr/sbin/nologin indexer
USER indexer
WORKDIR /home/indexer

ENTRYPOINT ["/usr/bin/tini", "--", "flyto-index"]
CMD ["--help"]
