#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
# Noninteractive local-source bootstrap; deploy/install.sh owns installation preparation.
set -eu
umask 077

fail() { printf 'Setup refused: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'HELP'
Usage: sh install.sh [--dir DIR] [--port PORT] [--project NAME]
                     [--image LOCAL_IMAGE | --build]

Run from a source checkout with Docker already running. No prompts are shown.
Fresh defaults: ~/bonafides, loopback port 8000, Compose project bonafides.
Parameters override fresh defaults; existing installations reuse saved settings.
--image uses an already-loaded image; application images are never pulled.
Reruns reuse saved project/port and the last successfully installed image.
--build explicitly rebuilds the current checkout instead of reusing that image.
Installation never starts or stops the application; use start.sh and stop.sh.
HELP
}

root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
dir=
port=8000
project=bonafides
image=
build=no
port_set=no
project_set=no
while [ "$#" -gt 0 ]; do
  case $1 in
    --help|-h) usage; exit 0 ;;
    --build) build=yes; shift ;;
    --dir|--port|--project|--image)
      [ "$#" -ge 2 ] && [ -n "$2" ] || fail "a value is required for $1"
      case $1 in
        --dir) dir=$2 ;;
        --port) port=$2; port_set=yes ;;
        --project) project=$2; project_set=yes ;;
        --image) image=$2 ;;
      esac
      shift 2 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done
[ "$build" = no ] || [ -z "$image" ] || fail "choose --build or --image, not both"
[ -n "$dir" ] || dir=${HOME:?set HOME or pass --dir}/bonafides
valid_port() {
  case $port in ''|*[!0-9]*) return 1 ;; esac
  [ "${#port}" -le 5 ] && [ "$port" -ge 1024 ] && [ "$port" -le 65535 ]
}
valid_project() {
  case $project in [a-z]*) ;; *) return 1 ;; esac
  case $project in *[!a-z0-9-]*) return 1 ;; esac
  [ "${#project}" -ge 3 ] && [ "${#project}" -le 48 ]
}

command -v docker >/dev/null 2>&1 || fail "install Docker Desktop (macOS) or Docker Engine with Compose v2 (Linux), start it, then rerun"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required; enable it in your existing Docker runtime"
platform=$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}') ||
  fail "start your Docker runtime and check its current context, then rerun; setup never changes contexts"
case $platform in linux/arm64|linux/amd64) ;; *) fail "unsupported Docker server platform: $platform" ;; esac
DOCKER_DEFAULT_PLATFORM=$platform
export DOCKER_DEFAULT_PLATFORM

case $dir in \~) dir=$HOME ;; \~/*) dir=$HOME/${dir#\~/} ;; esac
case $dir in /*) ;; *) dir=$PWD/$dir ;; esac
case $dir in *"'"*|*'
'*) fail "installation directory cannot contain a single quote or newline" ;; esac
if [ -d "$dir" ]; then dir=$(CDPATH='' cd -- "$dir" && pwd -P); fi
existing=no
if [ -e "$dir/.env" ] || [ -L "$dir/.env" ]; then
  [ -f "$dir/.env" ] && [ ! -L "$dir/.env" ] || fail "saved .env must be a regular file, not a symlink"
  # Read only two literal fields; never source/evaluate installation configuration.
  saved_project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$dir/.env")
  saved_port=$(sed -n 's/^BONAFIDES_PORT=//p' "$dir/.env")
  [ "$project_set" = no ] || [ "$project" = "$saved_project" ] || fail "existing installation requires its saved project; nothing was changed"
  [ "$port_set" = no ] || [ "$port" = "$saved_port" ] || fail "existing installation requires its saved port; nothing was changed"
  project=$saved_project
  port=$saved_port
  existing=yes
  printf 'Reusing saved installation settings in %s.\n' "$dir"
fi
valid_port || fail "port must be an integer from 1024 through 65535"
valid_project || fail "project must be 3–48 lowercase letters/digits/hyphens, starting with a letter"
# Normalize decimal ports, including input such as 08000, before comparing saved bindings.
port=$(printf '%s' "$port" | sed 's/^0*//')

directories=$(docker ps -a --filter "label=com.docker.compose.project=$project" \
  --format 'directory={{.Label "com.docker.compose.project.working_dir"}}') || fail "cannot inspect existing Compose projects"
volumes=$(docker volume ls --filter "label=com.docker.compose.project=$project" --format '{{.Name}}') || fail "cannot inspect existing volumes"
conflict=no
if [ -n "$directories" ]; then
  if printf '%s\n' "$directories" | grep -Fvx -- "directory=$dir" >/dev/null; then
    conflict=yes
  else
    status=$?
    [ "$status" -eq 1 ] || fail "cannot compare existing installation directories"
  fi
fi
if [ "$existing" = no ] && { [ -n "$directories" ] || [ -n "$volumes" ]; }; then conflict=yes; fi
[ "$conflict" = no ] || fail "project $project belongs to another installation; select another --project"

if [ -z "$image" ] && [ "$build" = no ] && [ -e "$dir/.installer-image" ]; then
  [ -f "$dir/.installer-image" ] && [ ! -L "$dir/.installer-image" ] || fail "saved image identity must be a regular file"
  image=$(cat "$dir/.installer-image")
  [ -n "$image" ] || fail "saved image identity is empty; choose --build or --image deliberately"
fi
if [ -n "$image" ]; then
  case $image in -*|*[!a-zA-Z0-9_./:@-]*) fail "invalid local image reference" ;; esac
  actual_platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image") || fail "local image is missing; use --build or load it and specify --image"
  [ "$actual_platform" = "$platform" ] || fail "image platform $actual_platform differs from native Docker platform $platform"
else
  command -v git >/dev/null 2>&1 || fail "Git is required for a source build; alternatively supply --image with an already-loaded image"
  docker buildx version >/dev/null 2>&1 || fail "Docker Buildx is required for a source build"
  [ -f "$root/Dockerfile" ] && [ -f "$root/pyproject.toml" ] || fail "run this entrypoint from a complete source checkout"
  revision=$(git -C "$root" rev-parse --verify HEAD) || fail "source checkout has no commit identity"
  [ "$(git -C "$root" rev-parse --show-toplevel)" = "$root" ] || fail "source root is not the Git checkout root"
  changes=$(git -C "$root" status --porcelain --untracked-files=normal) || fail "cannot inspect source changes"
  [ -z "$changes" ] || revision=$revision-dirty
  version=$(sed -n 's/^version = "\([^"]*\)"$/\1/p' "$root/pyproject.toml")
  [ -n "$version" ] || fail "cannot read project version"
  image="bonafides-local:$(printf '%.12s' "$revision")-${platform#linux/}-$(date +%s)-$$"
fi
printf '\nDirectory: %s\nProject:   %s\nURL:       http://localhost:%s\nPlatform:  %s\nImage:     %s\n' "$dir" "$project" "$port" "$platform" "$image"
if [ -n "${revision:-}" ]; then printf 'Build source: %s (%s). Base-image downloads may be needed.\n' "$revision" "$root"; fi
printf 'Existing credentials and volumes are retained. Stop an existing target before reinstalling.\n'

work=$(mktemp -d)
probe=
cleanup() {
  if [ -n "$probe" ]; then
    docker rm -f "$probe" >/dev/null || printf 'Warning: could not remove port-check container %s\n' "$probe" >&2
  fi
  rm -rf -- "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
if [ -n "${revision:-}" ]; then
  docker buildx build --pull --load --platform "$platform" --target app \
    --build-arg "VERSION=$version" --build-arg "REVISION=$revision" \
    --build-arg SOURCE=https://github.com/Mathews-Tom/BonaFides \
    --tag "$image" "$root"
fi

for asset in install.sh lifecycle.sh; do
  docker run --rm --pull never --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --entrypoint cat "$image" \
    "/app/deploy/$asset" > "$work/$asset" ||
    fail "image must provide the separate lifecycle scripts; rebuild with --build or choose a current local image"
done
contract=$(sh "$work/install.sh" --contract) ||
  fail "image does not support separate installation and startup; rebuild with --build"
[ "$contract" = bonafides-lifecycle-1 ] || fail "unsupported image lifecycle contract"
sh "$work/install.sh" --check --dir "$dir" --port "$port" --project "$project" "$image"

# A temporary native container reserves only the requested loopback port. This
# needs no host Python, lsof or netcat, and catches native as well as Docker listeners.
probe=$(docker create --pull never --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --publish "127.0.0.1:$port:8000" \
  --entrypoint python "$image" -c 'import time; time.sleep(60)') || fail "cannot create port-check container"
if ! docker start "$probe" > "$work/port.out" 2> "$work/port.err"; then
  docker rm -f "$probe" >/dev/null; probe=
  if ! grep -Eq 'address already in use|port is already allocated|ports are not available|Ports are not available' "$work/port.err"; then
    cat "$work/port.err" >&2
    fail "Docker could not start the port check"
  fi
  fail "port $port is occupied; its listener was not changed; choose another --port for a fresh installation"
fi
docker rm -f "$probe" >/dev/null; probe=

sh "$work/install.sh" --dir "$dir" --port "$port" --project "$project" "$image"
printf '\nInstallation prepared; the application is not started.\n'
printf "Start it with: sh '%s/start.sh'\n" "$dir"
