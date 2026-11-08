#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
set -eu
action=uninstall
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
. "$script_dir/lifecycle.sh"
dir=$script_dir
yes=no
purge=no
purge_confirmation=
while [ $# -gt 0 ]; do
  case $1 in
    --dir) [ $# -ge 2 ] || fail "--dir requires a directory"; dir=$2; shift 2 ;;
    --yes) yes=yes; shift ;;
    --purge) purge=yes; shift ;;
    --confirm-purge) [ $# -ge 2 ] || fail "--confirm-purge requires the exact project"; purge_confirmation=$2; shift 2 ;;
    *) fail "usage: uninstall.sh [--dir DIR] [--yes] [--purge [--confirm-purge PROJECT]]" ;;
  esac
done
[ "$purge" = yes ] || [ -z "$purge_confirmation" ] || fail "--confirm-purge requires --purge"
clean_environment
dir=$(canonical_directory "$dir")
load_saved
docker_ready
check_ownership
# Complete file preflight happens before stopping even one container. Unknown files are never
# claimed, followed or recursively removed, including additional files inside the secrets folder.
secret_names='admin_password migrator_password runtime_password migrator_dsn runtime_dsn keyring bootstrap_token local_model_route.json'
managed_files='.env .installer-image .installer-state compose.yaml init-db.sh start.sh stop.sh uninstall.sh lifecycle.sh models.sh'
if [ "$purge" = yes ]; then
  [ -d "$dir/secrets" ] && [ ! -L "$dir/secrets" ] || fail "secrets must be an owned private directory"
  [ "$(find "$dir/secrets" -prune -type d -user "$(id -u)" ! -perm -004 ! -perm -002 ! -perm -001 ! -perm -040 ! -perm -020 ! -perm -010 -print)" = "$dir/secrets" ] || fail "secrets must be an owned private directory"
  for name in $secret_names; do
    if [ -e "$dir/secrets/$name" ] || [ -L "$dir/secrets/$name" ]; then private_file "$dir/secrets/$name"; fi
  done
  for name in $managed_files; do
    if [ -e "$dir/$name" ] || [ -L "$dir/$name" ]; then
      [ -f "$dir/$name" ] && [ ! -L "$dir/$name" ] || fail "$dir/$name is not a managed regular file"
      [ "$(find "$dir/$name" -prune -type f -user "$(id -u)" -print)" = "$dir/$name" ] || fail "$dir/$name is owned by another user"
    fi
  done
fi
[ "$yes" = yes ] || confirm "Remove runtime containers and networks for $project (keep data, configuration and keys)" || fail "runtime removal needs confirmation; unattended callers must pass --yes"
if [ "$purge" = yes ]; then
  if [ -z "$purge_confirmation" ] && [ -t 0 ] && [ -t 1 ]; then
    printf 'Permanently delete managed data, configuration and keys. Type the exact project %s: ' "$project"
    IFS= read -r purge_confirmation || fail "purge confirmation was not received"
  fi
  [ "$purge_confirmation" = "$project" ] || fail "purge additionally requires the exact project through --confirm-purge or a typed confirmation; nothing was changed"
fi
# Recheck after operator prompts: an intervening foreign attachment must not turn consent into
# authority over a resource that was not owned when the prompt was displayed.
check_ownership
for container in $containers; do docker rm -f "$container" >/dev/null || fail "could not remove target container $container"; done
while IFS=' ' read -r kind logical physical _extra; do
  [ -n "$kind" ] || continue
  if [ "$kind" = network ] || [ "$purge" = yes ]; then
    if resource_exists "$kind" "$physical"; then
      docker "$kind" rm "$physical" >/dev/null || fail "could not remove managed $kind $physical"
    fi
  fi
done <<EOF
$resources
EOF
if [ "$purge" = yes ]; then
  for name in $secret_names; do rm -f -- "$dir/secrets/$name"; done
  # rmdir only succeeds if there are no unrelated entries; preserve them otherwise.
  remaining=$(find "$dir/secrets" -mindepth 1 -maxdepth 1 -exec printf x \; -quit) ||
    fail "could not inspect remaining secret-directory entries"
  [ -n "$remaining" ] || rmdir -- "$dir/secrets" ||
    fail "could not remove the empty managed secrets directory"
  for name in $managed_files; do rm -f -- "$dir/$name"; done
  printf 'Purged owned managed resources for %s. Unrelated files, images and Docker were retained.\n' "$project"
else
  printf 'Uninstalled runtime for %s. Data, configuration, keys and management scripts were retained in %s.\n' "$project" "$dir"
fi
