#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
set -eu
action=start
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
. "$script_dir/lifecycle.sh"
dir=$script_dir
open_browser=no
while [ $# -gt 0 ]; do
  case $1 in
    --dir) [ $# -ge 2 ] || fail "--dir requires a directory"; dir=$2; shift 2 ;;
    --open-browser) open_browser=yes; shift ;;
    *) fail "usage: start.sh [--dir DIR] [--open-browser]" ;;
  esac
done
clean_environment
dir=$(canonical_directory "$dir")
load_saved
[ "$model_route_enabled" = no ] || private_file "$dir/secrets/local_model_route.json"
docker_ready
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
check_ownership
docker image inspect "$image" >/dev/null 2>&1 || fail "saved application image is not loaded; load it or run install with a deliberately selected local image"
[ -f "$dir/compose.yaml" ] && [ ! -L "$dir/compose.yaml" ] || fail "managed Compose asset is missing or linked; run install while stopped"
check_install
check_proxy_network
if [ "$running" = yes ]; then
  # A healthy installation is not recreated and its one-shot jobs are not rerun.
  for service in db api worker ingress lifecycle source-egress renderer parser; do
    container=$(compose ps -q "$service") || fail "cannot inspect $service"
    [ -n "$container" ] || fail "installation is partially running; stop it before starting again"
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container") || fail "cannot inspect $service readiness"
    case $health in healthy|running) ;; *) fail "installation is not healthy; inspect its logs, then stop before starting again" ;; esac
  done
else
  create_resources
  check_ownership
  [ "$running" = no ] || fail "target started during preflight; inspect it before retrying"
  # Completed one-shot containers must not hide preparation/migrations for this start.
  compose rm -f prepare migrate >/dev/null || fail "cannot remove stopped preparation containers"
  compose up -d --wait --wait-timeout 420 || fail "services did not become healthy; inspect Docker Compose logs in $dir"
fi
compose exec -T api python -m bonafides init-bootstrap-if-ownerless || fail "bootstrap initialization failed; inspect the running installation"
printf 'BonaFides is ready. Sign in: %s\n' "$origin"
printf 'Secrets (keep private; escrow the keyring separately): %s/secrets\n' "$dir"
printf 'First-time owner setup (only while no owner exists):\n'
printf "  Read the setup token in your private terminal: cat '%s/secrets/bootstrap_token'\n" "$dir"
printf '  The setup token is valid for 15 minutes. Choose your own login and password when signing up.\n'
printf "  If the window expires before signup, run sh '%s/start.sh' again to renew it; no reinstall is needed.\n" "$dir"
printf '  After creating the owner, sign in with your password; the setup token cannot create another account.\n'
if [ "$open_browser" = yes ]; then
  case $(uname -s) in
    Darwin) open "$origin" || printf 'Browser did not open; use the URL above.\n' >&2 ;;
    *)
      if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$origin" || printf 'Browser did not open; use the URL above.\n' >&2
      else
        printf 'Open %s in your browser.\n' "$origin"
      fi ;;
  esac
fi
