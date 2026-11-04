#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
set -eu
action=stop
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
. "$script_dir/lifecycle.sh"
dir=$script_dir
while [ $# -gt 0 ]; do
  case $1 in
    --dir) [ $# -ge 2 ] || fail "--dir requires a directory"; dir=$2; shift 2 ;;
    *) fail "usage: stop.sh [--dir DIR]" ;;
  esac
done
clean_environment
dir=$(canonical_directory "$dir")
load_saved
docker_ready
check_ownership
for container in $containers; do docker stop "$container" >/dev/null || fail "could not stop target container $container"; done
printf 'Stopped %s. Containers, data, configuration and keys were retained.\n' "$project"
