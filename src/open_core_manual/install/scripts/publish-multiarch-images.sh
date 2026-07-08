#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
WORKSPACE="/Users/chester/flytohub"
IMAGE_REPOSITORY="${FLYTO_WARROOM_IMAGE_REPOSITORY:-docker.io/chesterhsu/flyto-warroom}"
PLATFORMS="${FLYTO_WARROOM_PLATFORMS:-linux/amd64,linux/arm64}"
PUSH=0
NO_CACHE=0

usage() {
  cat <<'USAGE'
Usage: publish-multiarch-images.sh [--push] [--no-cache] [--workspace PATH] [--repository IMAGE] [--platforms linux/amd64,linux/arm64]

Build Flyto2 Warroom CE Docker images for linux/amd64 and linux/arm64.
With docker buildx, the script publishes manifest-list tags directly.
Without buildx, it builds per-platform suffix tags and publishes a manifest list
with docker manifest create/push.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --push)
      PUSH=1
      shift
      ;;
    --no-cache)
      NO_CACHE=1
      shift
      ;;
    --workspace)
      WORKSPACE="$2"
      shift 2
      ;;
    --repository)
      IMAGE_REPOSITORY="$2"
      shift 2
      ;;
    --platforms)
      PLATFORMS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      WORKSPACE="$1"
      shift
      ;;
  esac
done

ENGINE_IMAGE="${FLYTO_WARROOM_ENGINE_IMAGE:-$IMAGE_REPOSITORY}"
ENGINE_TAG="${FLYTO_WARROOM_ENGINE_TAG:-engine-ce}"
WORKER_IMAGE="${FLYTO_WARROOM_WORKER_IMAGE:-$IMAGE_REPOSITORY}"
WORKER_TAG="${FLYTO_WARROOM_WORKER_TAG:-worker-ce}"
FRONTEND_IMAGE="${FLYTO_WARROOM_FRONTEND_IMAGE:-$IMAGE_REPOSITORY}"
FRONTEND_TAG="${FLYTO_WARROOM_FRONTEND_TAG:-code-ce}"
RUNNER_IMAGE="${FLYTO_WARROOM_RUNNER_IMAGE:-$IMAGE_REPOSITORY}"
RUNNER_TAG="${FLYTO_WARROOM_RUNNER_TAG:-runner-ce}"
VERIFICATION_IMAGE="${FLYTO_WARROOM_VERIFICATION_IMAGE:-$IMAGE_REPOSITORY}"
VERIFICATION_TAG="${FLYTO_WARROOM_VERIFICATION_TAG:-verification-ce}"
BRAND_VISION_IMAGE="${FLYTO_WARROOM_BRAND_VISION_IMAGE:-$IMAGE_REPOSITORY}"
BRAND_VISION_TAG="${FLYTO_WARROOM_BRAND_VISION_TAG:-brand-vision-ce}"
PDF_IMAGE="${FLYTO_WARROOM_PDF_IMAGE:-$IMAGE_REPOSITORY}"
PDF_TAG="${FLYTO_WARROOM_PDF_TAG:-pdf-ce}"

if [ "$PUSH" -ne 1 ]; then
  echo "dry-run: pass --push to publish images and manifest lists"
fi

python3 "$ROOT/install/scripts/audit-docker-build-boundary.py" "$WORKSPACE"

TMP_ROOT="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

CODE_CTX="$TMP_ROOT/flyto-code"

prepare_code_context() {
  mkdir -p "$CODE_CTX"
  tar -C "$WORKSPACE/flyto-code" -cf - . | tar -C "$CODE_CTX" -xf -
  rm -rf "$CODE_CTX/node_modules" \
    "$CODE_CTX/dist" \
    "$CODE_CTX/dist-next" \
    "$CODE_CTX/out" \
    "$CODE_CTX/test-results" \
    "$CODE_CTX/flyto-design-tokens-pkg"
  if [ -d "$WORKSPACE/flyto-design-tokens" ]; then
    cp -R "$WORKSPACE/flyto-design-tokens" "$CODE_CTX/flyto-design-tokens-pkg"
  else
    echo "missing $WORKSPACE/flyto-design-tokens" >&2
    exit 1
  fi
  mkdir -p "$CODE_CTX/public/i18n/code"
  if [ -d "$WORKSPACE/flyto-i18n/dist/code" ]; then
    cp -R "$WORKSPACE/flyto-i18n/dist/code/." "$CODE_CTX/public/i18n/code/"
  fi
  python3 - "$CODE_CTX/package.json" "$CODE_CTX/package-lock.json" "$CODE_CTX/flyto-design-tokens-pkg/package.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
lock_path = Path(sys.argv[2])
tokens_path = Path(sys.argv[3])
payload = json.loads(path.read_text(encoding="utf-8"))
for section in ("dependencies", "devDependencies"):
    deps = payload.get(section, {})
    for name, value in list(deps.items()):
        if name == "@flyto/design-tokens" or value in ("file:../flyto-design-tokens", "file:./vendor/@flyto/design-tokens"):
            deps[name] = "file:./flyto-design-tokens-pkg"
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
tokens["name"] = "@flyto/design-tokens"
tokens_path.write_text(json.dumps(tokens, indent=2) + "\n", encoding="utf-8")

if lock_path.exists():
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock.setdefault("packages", {})
    root_package = packages.setdefault("", {})
    root_package.setdefault("dependencies", {})["@flyto/design-tokens"] = "file:./flyto-design-tokens-pkg"
    for key in list(packages):
        if key == "../flyto-design-tokens" or key == "vendor/@flyto/design-tokens" or key.endswith("/flyto-design-tokens"):
            packages.pop(key, None)
    packages["flyto-design-tokens-pkg"] = {
        "name": "@flyto/design-tokens",
        "version": tokens.get("version", "0.1.0"),
        "license": tokens.get("license", "Apache-2.0"),
    }
    packages["node_modules/@flyto/design-tokens"] = {
        "resolved": "flyto-design-tokens-pkg",
        "link": True,
    }
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
PY
}

prepare_code_context

no_cache_args() {
  if [ "$NO_CACHE" -eq 1 ]; then
    printf '%s\n' "--no-cache"
  fi
}

platform_arch() {
  case "$1" in
    linux/amd64) printf '%s\n' "amd64" ;;
    linux/arm64) printf '%s\n' "arm64" ;;
    *)
      echo "unsupported platform: $1" >&2
      exit 1
      ;;
  esac
}

platform_os() {
  printf '%s\n' "${1%%/*}"
}

buildx_available() {
  docker buildx version >/dev/null 2>&1
}

buildx_build() {
  image="$1"
  tag="$2"
  context="$3"
  dockerfile="$4"
  shift 4
  if [ "$PUSH" -ne 1 ]; then
    echo "dry-run buildx $image:$tag $context"
    return 0
  fi
  docker buildx build \
    --platform "$PLATFORMS" \
    $(no_cache_args) \
    "$@" \
    -f "$dockerfile" \
    -t "$image:$tag" \
    --push \
    "$context"
}

buildx_build_engine_worker() {
  if [ "$PUSH" -ne 1 ]; then
    echo "dry-run buildx $ENGINE_IMAGE:$ENGINE_TAG and $WORKER_IMAGE:$WORKER_TAG $WORKSPACE/flyto-engine"
    return 0
  fi
  docker buildx build \
    --platform "$PLATFORMS" \
    $(no_cache_args) \
    -f "$WORKSPACE/flyto-engine/Dockerfile" \
    -t "$ENGINE_IMAGE:$ENGINE_TAG" \
    -t "$WORKER_IMAGE:$WORKER_TAG" \
    --push \
    "$WORKSPACE/flyto-engine"
}

legacy_build_one() {
  image="$1"
  tag="$2"
  context="$3"
  dockerfile="$4"
  platform="$5"
  shift 5
  arch="$(platform_arch "$platform")"
  os_name="$(platform_os "$platform")"
  suffix="$arch"
  if [ "$PUSH" -ne 1 ]; then
    echo "dry-run docker build --platform $platform $image:$tag-$suffix $context"
    return 0
  fi
  docker build \
    --platform "$platform" \
    --build-arg "TARGETOS=$os_name" \
    --build-arg "TARGETARCH=$arch" \
    $(no_cache_args) \
    "$@" \
    -f "$dockerfile" \
    -t "$image:$tag-$suffix" \
    "$context"
  docker push "$image:$tag-$suffix"
}

legacy_manifest() {
  image="$1"
  tag="$2"
  refs=""
  old_ifs="$IFS"
  IFS=","
  for platform in $PLATFORMS; do
    arch="$(platform_arch "$platform")"
    refs="$refs $image:$tag-$arch"
  done
  IFS="$old_ifs"
  if [ "$PUSH" -ne 1 ]; then
    echo "dry-run manifest $image:$tag <-$refs"
    return 0
  fi
  docker manifest rm "$image:$tag" >/dev/null 2>&1 || true
  docker manifest create "$image:$tag" $refs
  old_ifs="$IFS"
  IFS=","
  for platform in $PLATFORMS; do
    arch="$(platform_arch "$platform")"
    os_name="$(platform_os "$platform")"
    docker manifest annotate "$image:$tag" "$image:$tag-$arch" --os "$os_name" --arch "$arch"
  done
  IFS="$old_ifs"
  docker manifest push --purge "$image:$tag"
}

legacy_build() {
  image="$1"
  tag="$2"
  context="$3"
  dockerfile="$4"
  shift 4
  old_ifs="$IFS"
  IFS=","
  for platform in $PLATFORMS; do
    legacy_build_one "$image" "$tag" "$context" "$dockerfile" "$platform" "$@"
  done
  IFS="$old_ifs"
  legacy_manifest "$image" "$tag"
}

legacy_build_engine_worker() {
  old_ifs="$IFS"
  IFS=","
  for platform in $PLATFORMS; do
    arch="$(platform_arch "$platform")"
    os_name="$(platform_os "$platform")"
    suffix="$arch"
    if [ "$PUSH" -ne 1 ]; then
      echo "dry-run docker build --platform $platform $ENGINE_IMAGE:$ENGINE_TAG-$suffix and $WORKER_IMAGE:$WORKER_TAG-$suffix $WORKSPACE/flyto-engine"
      continue
    fi
    docker build \
      --platform "$platform" \
      --build-arg "TARGETOS=$os_name" \
      --build-arg "TARGETARCH=$arch" \
      $(no_cache_args) \
      -f "$WORKSPACE/flyto-engine/Dockerfile" \
      -t "$ENGINE_IMAGE:$ENGINE_TAG-$suffix" \
      "$WORKSPACE/flyto-engine"
    docker tag "$ENGINE_IMAGE:$ENGINE_TAG-$suffix" "$WORKER_IMAGE:$WORKER_TAG-$suffix"
    docker push "$ENGINE_IMAGE:$ENGINE_TAG-$suffix"
    docker push "$WORKER_IMAGE:$WORKER_TAG-$suffix"
  done
  IFS="$old_ifs"
  legacy_manifest "$ENGINE_IMAGE" "$ENGINE_TAG"
  legacy_manifest "$WORKER_IMAGE" "$WORKER_TAG"
}

if buildx_available; then
  echo "using docker buildx for $PLATFORMS"
  if [ "$PUSH" -eq 1 ]; then
    docker buildx inspect >/dev/null 2>&1 || docker buildx create --use
  fi
  buildx_build_engine_worker
  buildx_build "$RUNNER_IMAGE" "$RUNNER_TAG" "$WORKSPACE/flyto-engine/runner" "$WORKSPACE/flyto-engine/runner/Dockerfile"
  buildx_build "$VERIFICATION_IMAGE" "$VERIFICATION_TAG" "$WORKSPACE/flyto-core" "$WORKSPACE/flyto-core/Dockerfile.verification"
  buildx_build "$BRAND_VISION_IMAGE" "$BRAND_VISION_TAG" "$WORKSPACE/flyto-engine/brand-vision" "$WORKSPACE/flyto-engine/brand-vision/Dockerfile"
  buildx_build "$PDF_IMAGE" "$PDF_TAG" "$WORKSPACE/flyto-engine/pdf-service" "$WORKSPACE/flyto-engine/pdf-service/Dockerfile"
  buildx_build "$FRONTEND_IMAGE" "$FRONTEND_TAG" "$CODE_CTX" "$CODE_CTX/Dockerfile" \
    --build-arg "VITE_ENGINE_URL=${FLYTO_CODE_ENGINE_URL:-http://localhost:8080}" \
    --build-arg "VITE_AUTH_MODE=${FLYTO_CODE_AUTH_MODE:-local_jwt}" \
    --build-arg "VITE_AUTOMATION_URL=${FLYTO_AUTOMATION_URL:-http://localhost:8080}" \
    --build-arg "VITE_CORTEX_URL=${FLYTO_CORTEX_URL:-http://localhost:8080}"
else
  echo "docker buildx unavailable; using docker build --platform plus docker manifest"
  legacy_build_engine_worker
  legacy_build "$RUNNER_IMAGE" "$RUNNER_TAG" "$WORKSPACE/flyto-engine/runner" "$WORKSPACE/flyto-engine/runner/Dockerfile"
  legacy_build "$VERIFICATION_IMAGE" "$VERIFICATION_TAG" "$WORKSPACE/flyto-core" "$WORKSPACE/flyto-core/Dockerfile.verification"
  legacy_build "$BRAND_VISION_IMAGE" "$BRAND_VISION_TAG" "$WORKSPACE/flyto-engine/brand-vision" "$WORKSPACE/flyto-engine/brand-vision/Dockerfile"
  legacy_build "$PDF_IMAGE" "$PDF_TAG" "$WORKSPACE/flyto-engine/pdf-service" "$WORKSPACE/flyto-engine/pdf-service/Dockerfile"
  legacy_build "$FRONTEND_IMAGE" "$FRONTEND_TAG" "$CODE_CTX" "$CODE_CTX/Dockerfile" \
    --build-arg "VITE_ENGINE_URL=${FLYTO_CODE_ENGINE_URL:-http://localhost:8080}" \
    --build-arg "VITE_AUTH_MODE=${FLYTO_CODE_AUTH_MODE:-local_jwt}" \
    --build-arg "VITE_AUTOMATION_URL=${FLYTO_AUTOMATION_URL:-http://localhost:8080}" \
    --build-arg "VITE_CORTEX_URL=${FLYTO_CORTEX_URL:-http://localhost:8080}"
fi

if [ "$PUSH" -eq 1 ]; then
  python3 "$ROOT/install/scripts/verify-docker-images.py" --write-digests --timeout 180
fi
