#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
set -eu
action=models
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
. "$script_dir/lifecycle.sh"
failure_status=2
dir=$script_dir
[ $# -gt 0 ] || fail "usage: models.sh attach|qualify|remove [--dir DIR] [options]"
operation=$1; shift
model=; license=; authorized=no; stage=; reviewer=; import_structuring=
while [ $# -gt 0 ]; do
  case $1 in
    --dir) [ $# -ge 2 ] || fail "--dir requires a directory"; dir=$2; shift 2 ;;
    --model) [ $# -ge 2 ] || fail "--model requires a name"; model=$2; shift 2 ;;
    --model-license) [ $# -ge 2 ] || fail "--model-license requires a license"; license=$2; shift 2 ;;
    --authorize-local-model) authorized=yes; shift ;;
    --import-structuring) import_structuring=--import-structuring; shift ;;
    --stage) [ $# -ge 2 ] || fail "--stage requires a stage"; stage=$2; shift 2 ;;
    --reviewed-by) [ $# -ge 2 ] || fail "--reviewed-by requires a name"; reviewer=$2; shift 2 ;;
    *) fail "unknown option: $1" ;;
  esac
done
case $operation in
  attach)
    [ -n "$model" ] && [ -n "$license" ] && [ "$authorized" = yes ] || fail "attach requires --model, --model-license and --authorize-local-model"
    [ -z "$stage$reviewer" ] || fail "stage/reviewer belong to qualify" ;;
  qualify)
    case $stage in generate_documents|regenerate_section|structure_import) ;; *) fail "choose generate_documents, regenerate_section or structure_import" ;; esac
    [ -n "$reviewer" ] || fail "qualify requires --reviewed-by"
    [ -z "$model$license$import_structuring" ] && [ "$authorized" = no ] || fail "model/authorization/import-structuring belong to attach" ;;
  remove)
    [ -z "$model$license$stage$reviewer$import_structuring" ] && [ "$authorized" = no ] || fail "remove accepts only --dir" ;;
  *) fail "choose attach, qualify or remove" ;;
esac
clean_environment
dir=$(canonical_directory "$dir")
load_saved
docker_ready
check_ownership
check_install
route_existed=no
route="$dir/secrets/local_model_route.json"
if [ "$operation" = attach ]; then
  [ ! -e "$route" ] && [ ! -L "$route" ] || fail "a route already exists; remove it deliberately before attaching another"
else
  if [ "$model_route_enabled" != yes ]; then
    [ "$operation" = remove ] && [ -e "$route" ] || fail "no managed model route is attached"
    # Interrupted attach or removal can leave a route after opt-in is disabled.
  fi
  if [ "$operation" = qualify ]; then private_file "$route"; fi
  if [ -e "$route" ] || [ -L "$route" ]; then
    [ -f "$route" ] && [ ! -L "$route" ] || fail "route must be a regular file, not a link"
    [ "$(find "$route" -prune -type f -user "$(id -u)" -print)" = "$route" ] || fail "route belongs to another user"
    route_existed=yes
  fi
fi
# Serialize supported model commands. Snapshots reject observed external changes, but
# installation files must not be edited manually while a model command is running.
umask 077
lock="$dir/.model-route-lock"
mkdir "$lock" 2>/dev/null || fail "another model command is active; do not remove its lock while it runs"
work=
cleanup() {
  if [ -n "$work" ]; then
    rm -f -- "$work/route.json" "$work/original-route" "$work/original-env" "$work/new-env"
    rmdir -- "$work" || true
  fi
  rmdir -- "$lock" || true
}
qualification_done=no
trap 'status=$?; cleanup; if [ "$status" -ne 0 ] && [ "$qualification_done" = no ]; then exit 2; fi' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
work=$(mktemp -d "$dir/.model-route.XXXXXX")
cp "$dir/.env" "$work/original-env"
if [ "$route_existed" = yes ]; then
  cp "$route" "$work/original-route"
  [ "$operation" != qualify ] || cp "$route" "$work/route.json"
fi
run_model() {
  docker run --rm --name "${project}-model-operation" --pull never --network "${project}_sources" --read-only \
    --cap-drop ALL --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
    --pids-limit 32 --memory 512m --cpus 1 \
    --mount "type=bind,source=$work,target=/work" --entrypoint python "$image" "$@"
}
result=0
if [ "$operation" != remove ]; then
  # Starts only this ownership-checked managed installation; never pulls a model.
  sh "$dir/start.sh" >/dev/null || fail "installation could not start; result was not applied"
  if [ "$operation" = attach ]; then
    run_model -m bonafides.installed_model attach --route /work/route.json \
      --model "$model" --model-license "$license" --authorize-local-model ${import_structuring:+"$import_structuring"} || exit $?
  else
    run_model -m bonafides models qualify --route /work/route.json --stage "$stage" \
      --dataset /app/qualification --reviewed-by "$reviewer" || result=$?
    case $result in 0|1) ;; *) exit 2 ;; esac
    run_model -m bonafides.installed_model verify-result --route /work/route.json \
      --original /work/original-route --stage "$stage" --result "$result" || fail "qualification did not publish the expected receipt"
  fi
  private_file "$work/route.json"
fi
private_file "$dir/.env"
cmp -s "$dir/.env" "$work/original-env" || fail "managed environment changed; result was not applied"
if [ "$route_existed" = yes ]; then
  [ -f "$route" ] && [ ! -L "$route" ] || fail "route changed; result was not applied"
  cmp -s "$route" "$work/original-route" || fail "route changed; result was not applied"
else
  [ ! -e "$route" ] && [ ! -L "$route" ] || fail "route appeared; result was not applied"
fi
printf 'Applying model route change; restarting this installation.\n'
sh "$dir/stop.sh" || fail "installation could not stop; result was not applied"
# Recheck after stopping, before publishing. Failed qualification receipts are genuine results.
load_saved
cmp -s "$dir/.env" "$work/original-env" || fail "managed environment changed; result was not applied"
if [ "$route_existed" = yes ]; then
  [ -f "$route" ] && [ ! -L "$route" ] || fail "route changed; result was not applied"
  cmp -s "$route" "$work/original-route" || fail "route changed; result was not applied"
else
  [ ! -e "$route" ] && [ ! -L "$route" ] || fail "route appeared; result was not applied"
fi
if [ "$operation" = remove ]; then
  printf '%s\n' "$expected_env" > "$work/new-env"
  mv -f -- "$work/new-env" "$dir/.env"
  rm -f -- "$route"
else
  # Workdir and secrets share the install filesystem, so renames are atomic and retain0600.
  mv -f -- "$work/route.json" "$route"
  printf '%s\n%s\n' "$expected_env" "$model_route_env" > "$work/new-env"
  mv -f -- "$work/new-env" "$dir/.env"
fi
sh "$dir/start.sh" >/dev/null || fail "route configuration was saved, but restart failed; resolve the error and run start.sh"
if [ "$operation" = remove ]; then
  printf 'Model route disabled. Owner data and Ollama models were retained.\n'
else
  printf 'Model route applied. Check each stage in Documents; attaching alone does not qualify it.\n'
fi
qualification_done=yes
exit "$result"
