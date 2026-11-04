#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
exec sh "$root/deploy/uninstall.sh" --dir "${HOME:?set HOME}/bonafides" "$@"
