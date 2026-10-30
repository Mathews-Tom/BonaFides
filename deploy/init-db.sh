#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
set -eu
# Passwords are passed over stdin, never process arguments, environment or output.
# Local setup generates hex-only credentials; reject anything else before SQL quoting.
migrator=$(cat /run/bonafides-db-secrets/migrator_password)
runtime=$(cat /run/bonafides-db-secrets/runtime_password)
case "$migrator:$runtime" in *[!a-f0-9:]*) exit 1;; esac
[ "${#migrator}" -eq 64 ] && [ "${#runtime}" -eq 64 ]
{
  printf "CREATE ROLE bonafides_migrator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '%s';\n" "$migrator"
  printf "CREATE ROLE bonafides_runtime LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '%s';\n" "$runtime"
  printf '%s\n' \
    'REVOKE ALL ON DATABASE bonafides FROM PUBLIC;' \
    'GRANT CONNECT ON DATABASE bonafides TO bonafides_migrator, bonafides_runtime;' \
    'GRANT CREATE ON DATABASE bonafides TO bonafides_migrator;' \
    'GRANT TEMPORARY ON DATABASE bonafides TO bonafides_migrator WITH GRANT OPTION;' \
    'GRANT TEMPORARY ON DATABASE bonafides TO bonafides_runtime;' \
    'REVOKE ALL ON SCHEMA public FROM PUBLIC;'
} | psql --username postgres --dbname bonafides --no-psqlrc --set ON_ERROR_STOP=1 --quiet
unset migrator runtime
