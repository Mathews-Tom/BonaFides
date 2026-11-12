#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
# Prepare an installation from a locally loaded image. Never start or stop application services.
set -eu
action=install
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
. "$script_dir/lifecycle.sh"
usage() { fail "usage: install.sh [--check] [--dir DIR] [--port PORT] [--project NAME] [--origin HTTPS_ORIGIN] [--proxy-network EXISTING_NETWORK] IMAGE | --contract"; }
image=${BONAFIDES_IMAGE:-}
dir=${BONAFIDES_INSTALL_DIR:-}
port=8000
project=bonafides
port_set=no
project_set=no
origin=
origin_set=no
proxy_network=
proxy_network_set=no
proxy_network_id=
check=no
while [ $# -gt 0 ]; do
  case $1 in
    --contract) printf 'bonafides-lifecycle-1\n'; exit 0 ;;
    --check) check=yes; shift ;;
    --dir) [ $# -ge 2 ] || usage; dir=$2; shift 2 ;;
    --port) [ $# -ge 2 ] || usage; port=$2; port_set=yes; shift 2 ;;
    --project) [ $# -ge 2 ] || usage; project=$2; project_set=yes; shift 2 ;;
    --origin) [ $# -ge 2 ] && [ -n "$2" ] || usage; origin=$2; origin_set=yes; shift 2 ;;
    --proxy-network) [ $# -ge 2 ] && [ -n "$2" ] || usage; proxy_network=$2; proxy_network_set=yes; shift 2 ;;
    -*) usage ;;
    *) [ -z "${image_argument:-}" ] || usage; image=$1; image_argument=yes; shift ;;
  esac
done
valid_image || usage
requested_image=$image
[ -n "$dir" ] || dir=${HOME:?set HOME or pass --dir}/bonafides
valid_port || fail "port must be an integer from 1024 through 65535"
# Normalize only validated input, before load_saved replaces the requested port.
port=$(printf '%s' "$port" | sed 's/^0*//')
requested_project=$project
requested_port=$port
requested_origin=$origin
requested_proxy_network=$proxy_network
if [ "$origin_set" = yes ]; then
  case $origin in https://*) ;; *) fail "--origin requires a canonical HTTPS origin" ;; esac
fi
clean_environment
dir=$(canonical_directory "$dir")
managed=no
if [ -e "$dir/.installer-state" ] || [ -L "$dir/.installer-state" ]; then
  load_saved
  [ "$project_set" = no ] || [ "$project" = "$requested_project" ] || fail "saved project differs; use the original project"
  [ "$port_set" = no ] || [ "$port" = "$requested_port" ] || fail "saved port differs; use the original port"
  [ "$origin_set" = no ] || [ "$origin" = "$requested_origin" ] || fail "saved origin differs; use the original origin"
  [ "$proxy_network_set" = no ] || [ "$proxy_network" = "$requested_proxy_network" ] || fail "saved proxy network differs; use the original network"
  managed=yes
elif [ -e "$dir/.env" ] || [ -L "$dir/.env" ] || [ -e "$dir/secrets" ] || [ -L "$dir/secrets" ]; then
  fail "existing configuration has no lifecycle ownership metadata; preserve it and use its original management procedure, or choose a separate empty installation"
fi
image=$requested_image
valid_project || fail "project must be 3–48 lowercase letters/digits/hyphens, starting with a letter"
valid_port || fail "port must be an integer from 1024 through 65535"
[ -n "$origin" ] || origin="http://localhost:$port"
docker_ready
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
check_proxy_network
image=$(docker image inspect --format '{{.Id}}' "$image") || fail "application image is not loaded; load it with docker load (this installer never pulls it)"
valid_image || fail "Docker returned an invalid image identity"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
docker run --rm --pull never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --entrypoint cat "$image" \
  /app/deploy/compose.release.yaml > "$work/compose.yaml" || fail "image has no release Compose asset"
# Resolve the image's actual physical resource names without reading or writing target config.
resources=$(BONAFIDES_IMAGE=$image BONAFIDES_SECRET_DIR=$dir/secrets docker compose \
  --project-name "$project" --project-directory "$work" --env-file /dev/null \
  -f "$work/compose.yaml" config --format json | \
  docker run --rm --pull never --interactive --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --entrypoint python "$image" -c '
import json, sys
config = json.load(sys.stdin)
for kind, key in (("volume", "volumes"), ("network", "networks")):
    for logical, resource in sorted(config[key].items()):
        if resource.get("external"):
            raise SystemExit("external resources cannot be managed by this installer")
        print(kind, logical, resource["name"])
') || fail "cannot resolve managed resources from the image"
validate_resources
# Retained metadata must still describe this image's exact managed resource set.
if [ "$managed" = yes ]; then
  saved_resources=$(sed '1,3d' "$dir/.installer-state")
  [ "$resources" = "$saved_resources" ] || fail "image changes the managed resource contract; no installation state was changed"
fi
check_ownership
[ "$running" = no ] || fail "target is running; use its stop.sh before installing (install never stops it)"
setup() {
  set -- "$@" --entrypoint python "$image" -m bonafides.local_setup \
    --release-root /install --host-root "$dir" --project "$project" --port "$port" \
    --proxy-network "$proxy_network" --proxy-network-id "$proxy_network_id" \
    --image "$image" --resource-map "$resources"
  case $origin in https://*) set -- "$@" --origin "$origin" ;; esac
  docker run --rm --pull never --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
    "$@" ${setup_check:+--check}
}
setup_check=yes
if [ -d "$dir" ]; then
  setup --mount "type=bind,source=$dir,target=/install,readonly" >/dev/null || fail "configuration preflight was refused; nothing was changed"
else
  setup >/dev/null || fail "configuration preflight was refused; nothing was changed"
fi
if [ "$check" = yes ]; then
  printf 'Installation preflight passed for %s in %s.\n' "$project" "$dir"
  exit 0
fi
check_proxy_network
[ -d "$dir" ] || { mkdir -p -- "$dir" && chmod 755 "$dir"; }
# Recheck after read-only preflight, before creating credentials or replacing assets.
check_ownership
[ "$running" = no ] || fail "target started during preflight; stop it deliberately before installing"
setup_check=
state=$(setup --mount "type=bind,source=$dir,target=/install") || fail "credential or asset setup was refused; no credential was deleted or rotated"
printf 'BonaFides installed in %s (credentials %s). No application service was started.\n' "$dir" "$state"
printf 'Secrets (keep private; escrow the keyring separately): %s/secrets\n' "$dir"
printf 'Start: %s/start.sh\nStop: %s/stop.sh\nUninstall (retains data): %s/uninstall.sh\n' "$dir" "$dir" "$dir"
