#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
for argument in "$@"; do
  if [ "$argument" = --dir ]; then
    exec sh "$root/deploy/uninstall.sh" "$@"
  fi
done
exec sh "$root/deploy/uninstall.sh" --dir "${HOME:?set HOME}/bonafides" "$@"
