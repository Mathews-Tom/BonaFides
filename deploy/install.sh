#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
# Install or update BonaFides from an application image that is already loaded locally.
# Needs only a shell and Docker Compose: no source checkout, Python or uv on the host.
# The application image is never pulled; credentials and volumes are never deleted or rotated.
set -eu

usage() {
  echo "usage: install.sh [--dir DIR] [--port PORT] [--project NAME] IMAGE" >&2
  echo "       IMAGE must already be loaded (docker load); BONAFIDES_IMAGE may supply it." >&2
  exit 2
}
fail() {
  echo "install refused: $*" >&2
  exit 1
}

image=${BONAFIDES_IMAGE:-}
dir=${BONAFIDES_INSTALL_DIR:-}
port=8000
project=bonafides
while [ $# -gt 0 ]; do
  case $1 in
    --dir) [ $# -ge 2 ] || usage; dir=$2; shift 2 ;;
    --port) [ $# -ge 2 ] || usage; port=$2; shift 2 ;;
    --project) [ $# -ge 2 ] || usage; project=$2; shift 2 ;;
    -*) usage ;;
    *) image=$1; shift ;;
  esac
done
[ -n "$image" ] || usage
[ -n "$dir" ] || dir=${HOME:?set HOME or pass --dir}/bonafides
# Ambient settings must not override the installed configuration.
for name in $(env | sed -n 's/=.*//p' | grep -E '^(BONAFIDES|COMPOSE)_' || true); do
  unset "$name"
done

command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
docker image inspect "$image" >/dev/null 2>&1 ||
  fail "image $image is not loaded locally; load it with docker load (this installer never pulls it)"

started=$(date +%s)
case $dir in /*) ;; *) dir=$(pwd)/$dir ;; esac
# Directories on the path must stay traversable: the database container reads init-db.sh from here.
[ -d "$dir" ] || { mkdir -p "$dir" && chmod 755 "$dir"; }
dir=$(cd "$dir" && pwd -P)

# Credentials and assets are written by the image's own module as the invoking user, so every file
# is host-owned and private. A partial or invalid state is refused with guidance and left untouched.
state=$(docker run --rm --pull never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$dir,target=/install" --entrypoint python "$image" \
  -m bonafides.local_setup --release-root /install --host-root "$dir" \
  --project "$project" --port "$port") ||
  fail "credential setup was refused above; no credential was deleted or rotated"

port=$(sed -n 's/^BONAFIDES_PORT=//p' "$dir/.env")
BONAFIDES_IMAGE=$image
export BONAFIDES_IMAGE
compose() {
  docker compose --project-directory "$dir" --env-file "$dir/.env" -f "$dir/compose.yaml" "$@"
}

# Stopping first keeps the one-shot prepare and migrate services fresh and never races a running
# database; named volumes (database, blobs, secrets) are kept.
api=$(compose ps -q api 2>/dev/null | head -n 1 || true)
previous=
[ -z "$api" ] || previous=$(docker inspect --format '{{.Image}}' "$api" 2>/dev/null || true)
compose down --remove-orphans
compose up -d --wait --wait-timeout 420 || {
  [ -z "$previous" ] || echo "previous image id (to return to it, rerun with it as IMAGE): $previous" >&2
  fail "services did not become healthy; inspect: BONAFIDES_IMAGE=$image docker compose --project-directory $dir logs"
}
bootstrap=$(compose exec -T api python -m bonafides init-bootstrap-if-ownerless) ||
  fail "bootstrap initialization failed; the installation is running but sign-in is not set up"

echo "BonaFides is ready in $(($(date +%s) - started)) s (credentials $state)."
echo "Sign in: http://localhost:$port"
echo "$bootstrap"
echo "Secrets (keep private; escrow the keyring separately): $dir/secrets"
echo "Creating the owner (only while none exists) needs the token in: $dir/secrets/bootstrap_token"
echo "To manage later: BONAFIDES_IMAGE=$image docker compose --project-directory $dir ..."
