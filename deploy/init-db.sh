#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
set -eu
# Passwords are passed over stdin, never process arguments, environment or output.
# Local setup generates hex-only credentials; reject anything else before SQL quoting.
migrator=$(cat /run/offerloom-db-secrets/migrator_password)
runtime=$(cat /run/offerloom-db-secrets/runtime_password)
case "$migrator:$runtime" in *[!a-f0-9:]*) exit 1;; esac
[ "${#migrator}" -eq 64 ] && [ "${#runtime}" -eq 64 ]
{
  printf "CREATE ROLE offerloom_migrator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '%s';\n" "$migrator"
  printf "CREATE ROLE offerloom_runtime LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '%s';\n" "$runtime"
  printf '%s\n' 'REVOKE ALL ON DATABASE offerloom FROM PUBLIC;' 'GRANT CONNECT ON DATABASE offerloom TO offerloom_migrator, offerloom_runtime;' 'GRANT CREATE ON DATABASE offerloom TO offerloom_migrator;' 'REVOKE ALL ON SCHEMA public FROM PUBLIC;'
} | psql --username postgres --dbname offerloom --no-psqlrc --set ON_ERROR_STOP=1 --quiet
unset migrator runtime
