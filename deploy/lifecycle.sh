#!/bin/sh
# SPDX-License-Identifier: MIT OR Apache-2.0
# Shared by the image-owned installer and the installed management commands.

failure_status=1
fail() { printf '%s refused: %s\n' "${action:-lifecycle}" "$*" >&2; exit "$failure_status"; }

clean_environment() {
  for name in $(env | sed -n 's/=.*//p' | grep -E '^(BONAFIDES|COMPOSE)_' || true); do
    unset "$name"
  done
}

docker_ready() {
  command -v docker >/dev/null 2>&1 || fail "Docker is required"
  docker info >/dev/null 2>&1 || fail "Docker is not available; start the existing Docker installation"
}

canonical_directory() (
  case $1 in
    *"'"*|*','*|*'|'*|*'
'*) fail "installation path cannot contain quotes, commas, pipes or newlines" ;;
  esac
  if [ -d "$1" ]; then CDPATH='' cd -- "$1" && pwd -P; return; fi
  [ ! -e "$1" ] && [ ! -L "$1" ] || fail "installation path is not a directory"
  parent=$(dirname -- "$1")
  base=$(basename -- "$1")
  case $base in .|..) fail "normalize the installation path first" ;; esac
  parent=$(canonical_directory "$parent") || exit 1
  printf '%s/%s\n' "${parent%/}" "$base"
)

private_file() {
  [ -f "$1" ] && [ ! -L "$1" ] || fail "$1 must be a regular file, not a symlink"
  [ "$(find "$1" -prune -type f -user "$(id -u)" ! -perm -004 ! -perm -002 ! -perm -001 ! -perm -040 ! -perm -020 ! -perm -010 -print)" = "$1" ] ||
    fail "$1 must be private and owned by the current user"
}

valid_project() {
  case $project in [a-z]*) ;; *) return 1 ;; esac
  case $project in *[!a-z0-9-]*) return 1 ;; esac
  [ ${#project} -ge 3 ] && [ ${#project} -le 48 ]
}
valid_port() {
  case $port in ''|*[!0-9]*) return 1 ;; esac
  [ ${#port} -le 5 ] && [ "$port" -ge 1024 ] && [ "$port" -le 65535 ]
}
valid_image() {
  case $image in ''|-*|*[!a-zA-Z0-9_./:@-]*) return 1 ;; esac
}

valid_proxy_network() {
  case $proxy_network in ''|[!a-zA-Z0-9]*|*[!a-zA-Z0-9_.-]*) return 1 ;; esac
  [ ${#proxy_network} -le 128 ]
}

check_proxy_network() {
  [ -n "$proxy_network" ] || return 0
  valid_proxy_network || fail "invalid proxy network name"
  case $proxy_network in "$project"_*) fail "proxy network must be external to managed resources" ;; esac
  network_details=$(docker network inspect --format '{{.Id}}|{{.Driver}}|{{.Attachable}}|{{.ConfigOnly}}|{{index .Options "com.docker.network.bridge.default_bridge"}}' "$proxy_network") ||
    fail "operator proxy network does not exist or cannot be inspected; lifecycle never creates it"
  IFS='|' read -r actual_network_id network_driver network_attachable network_config_only default_bridge <<EOF
$network_details
EOF
  printf '%s\n' "$actual_network_id" | grep -Eq '^([a-z0-9]{25}|[0-9a-f]{64})$' || fail "invalid Docker network identity"
  [ -z "$proxy_network_id" ] || [ "$proxy_network_id" = "$actual_network_id" ] ||
    fail "operator proxy network was replaced; restore the saved network deliberately"
  [ "$network_config_only" != true ] || fail "operator proxy network is configuration-only and cannot attach containers"
  case $network_driver in
    overlay) [ "$network_attachable" = true ] || fail "operator overlay proxy network must already be attachable" ;;
    host|null) fail "operator proxy network must support standalone container attachment and DNS aliases" ;;
    bridge) [ "$default_bridge" != true ] || fail "default Docker bridge does not support the required proxy DNS alias; select an existing user-defined network" ;;
  esac
  proxy_network_id=$actual_network_id
}

# Metadata contains no secrets and is never executed. Its exact namespace binds every resource
# to both the Compose project and the canonical host directory, including orphaned volumes.
validate_resources() {
  expected=$(for logical in app-secrets blobs db-secrets import-spool migration-secrets pg spool; do
    printf 'volume %s %s_%s\n' "$logical" "$project" "$logical"
  done
  for logical in ingress private source-outbound sources; do
    printf 'network %s %s_%s\n' "$logical" "$project" "$logical"
  done)
  [ "$resources" = "$expected" ] || fail "managed resource metadata differs from this lifecycle contract; nothing was changed"
}

load_saved() {
  : "${dir:?lifecycle caller must select an installation directory}"
  if [ ! -e "$dir/.installer-state" ] && [ ! -L "$dir/.installer-state" ]; then
    fail "no completed managed installation found at $dir; run sh install.sh from the source checkout first"
  fi
  private_file "$dir/.installer-state"
  {
    IFS= read -r contract || fail "incomplete lifecycle metadata"
    IFS= read -r project || fail "incomplete lifecycle metadata"
    IFS= read -r saved_dir || fail "incomplete lifecycle metadata"
    resources=$(cat)
  } < "$dir/.installer-state"
  [ "$contract" = bonafides-lifecycle-1 ] && [ "$saved_dir" = "$dir" ] ||
    fail "installation metadata belongs to another directory or lifecycle contract"
  valid_project || fail "invalid saved project"
  validate_resources
  private_file "$dir/.env"
  port=$(sed -n 's/^BONAFIDES_PORT=//p' "$dir/.env")
  valid_port || fail "invalid saved port"
  origin=$(sed -n 's/^BONAFIDES_ORIGIN=//p' "$dir/.env")
  proxy_network=$(sed -n 's/^BONAFIDES_PROXY_NETWORK=//p' "$dir/.env")
  proxy_network_id=$(sed -n 's/^BONAFIDES_PROXY_NETWORK_ID=//p' "$dir/.env")
  expected_env=$(printf "COMPOSE_PROJECT_NAME=%s\nBONAFIDES_SECRET_DIR='%s/secrets'\nBONAFIDES_PORT=%s\nBONAFIDES_ORIGIN=%s\nBONAFIDES_MODE=production" "$project" "$dir" "$port" "$origin")
  if [ -n "$proxy_network" ] || [ -n "$proxy_network_id" ]; then
    valid_proxy_network || fail "invalid saved proxy network"
    printf '%s\n' "$proxy_network_id" | grep -Eq '^([a-z0-9]{25}|[0-9a-f]{64})$' || fail "invalid saved proxy network identity"
    expected_env=$(printf '%s\nBONAFIDES_PROXY_NETWORK=%s\nBONAFIDES_PROXY_NETWORK_ID=%s' "$expected_env" "$proxy_network" "$proxy_network_id")
  fi
  model_route_enabled=no
  model_route_env='BONAFIDES_LOCAL_MODEL_ROUTE_FILE=/run/bonafides/local_model_route.json'
  saved_env=$(cat "$dir/.env")
  if [ "$saved_env" = "$expected_env
$model_route_env" ]; then
    model_route_enabled=yes
  else
    [ "$saved_env" = "$expected_env" ] || fail "saved environment differs from the managed configuration; reconcile it deliberately"
  fi
  private_file "$dir/.installer-image"
  image=$(cat "$dir/.installer-image")
  valid_image || fail "invalid saved image identity"
}

check_install() {
  set --
  case $origin in https://*) set -- --origin "$origin" ;; esac
  docker run --rm --pull never --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
    --mount "type=bind,source=$dir,target=/install,readonly" --entrypoint python "$image" \
    -m bonafides.local_setup --release-root /install --host-root "$dir" \
    "$@" --proxy-network "$proxy_network" --proxy-network-id "$proxy_network_id" \
    --project "$project" --port "$port" --image "$image" --resource-map "$resources" \
    --check --validate-installed >/dev/null || fail "saved credentials or assets were refused"
}

resource_exists() {
  # Listing first distinguishes an absent resource from an inspect/daemon failure.
  names=$(docker "$1" ls --format '{{.Name}}') || fail "cannot list $1 resources"
  printf '%s\n' "$names" | grep -Fxq -- "$2"
}

check_ownership() {
  containers=$(docker ps -aq --filter "label=com.docker.compose.project=$project") || fail "cannot inspect project containers"
  [ "${managed:-yes}" = yes ] || [ -z "$containers" ] || fail "project already has runtime containers without this installation's metadata; nothing was changed"
  running=no
  for container in $containers; do
    binding=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$container") || fail "cannot inspect container ownership"
    [ "$binding" = "$dir" ] || fail "project $project has a container bound to another installation; nothing was changed"
    state=$(docker inspect --format '{{.State.Running}}' "$container") || fail "cannot inspect container state"
    # The sourcing entrypoint consumes this output flag.
    # shellcheck disable=SC2034
    case $state in true) running=yes ;; false) ;; *) fail "unknown container state" ;; esac
  done
  # Every expected physical name is checked, not merely resources returned by a project filter.
  # A foreign unlabelled volume with the same name must never be adopted by Compose.
  while IFS=' ' read -r kind logical physical _extra; do
    [ -n "$kind" ] || continue
    if resource_exists "$kind" "$physical"; then
      [ "${managed:-yes}" = yes ] || fail "$kind $physical already exists without this installation's metadata; nothing was changed"
      owner=$(docker "$kind" inspect --format '{{index .Labels "com.docker.compose.project"}}' "$physical") || fail "cannot inspect $kind ownership"
      binding=$(docker "$kind" inspect --format '{{index .Labels "io.bonafides.installation"}}' "$physical") || fail "cannot inspect $kind installation binding"
      label=$(docker "$kind" inspect --format "{{index .Labels \"com.docker.compose.$kind\"}}" "$physical") || fail "cannot inspect $kind identity"
      [ "$owner" = "$project" ] && [ "$binding" = "$dir" ] && [ "$label" = "$logical" ] ||
        fail "$kind $physical belongs to another or unverified installation; nothing was changed"
      # Even an owned volume/network must not be removed or rewritten under foreign containers.
      users=$(docker ps -aq --filter "$kind=$physical") || fail "cannot inspect $kind consumers"
      for user in $users; do
        case " $containers " in *" $user "*) ;; *)
          # Docker returns IDs separated by newlines, so use exact line matching as well.
          printf '%s\n' "$containers" | grep -Fxq -- "$user" || fail "$kind $physical is used by a foreign container; nothing was changed" ;;
        esac
      done
    else
      status=$?
      [ "$status" -eq 1 ] || fail "cannot determine $kind existence"
    fi
  done <<EOF
$resources
EOF
  # Unexpected project-labelled resources indicate a mismatched or incomplete installation.
  for kind in volume network; do
    owned=$(docker "$kind" ls --filter "label=com.docker.compose.project=$project" --format '{{.Name}}') || fail "cannot inspect project $kind resources"
    for physical in $owned; do
      printf '%s\n' "$resources" | grep -Eq "^$kind [a-z-]+ $physical$" || fail "unexpected project $kind $physical; nothing was changed"
    done
  done
}

create_resources() {
  while IFS=' ' read -r kind logical physical _extra; do
    [ -n "$kind" ] || continue
    if resource_exists "$kind" "$physical"; then continue; fi
    if [ "$kind" = network ] && { [ "$logical" = private ] || [ "$logical" = sources ]; }; then
      docker network create --internal --label "com.docker.compose.project=$project" --label "com.docker.compose.network=$logical" --label "io.bonafides.installation=$dir" "$physical" >/dev/null
    else
      docker "$kind" create --label "com.docker.compose.project=$project" --label "com.docker.compose.$kind=$logical" --label "io.bonafides.installation=$dir" "$physical" >/dev/null
    fi
  done <<EOF
$resources
EOF
}

compose() {
  if [ -n "$proxy_network" ]; then
    set -- -f "$dir/compose.proxy.yaml" "$@"
  fi
  BONAFIDES_IMAGE=$image docker compose --project-name "$project" --project-directory "$dir" --env-file "$dir/.env" -f "$dir/compose.yaml" "$@"
}

confirm() {
  [ -t 0 ] && [ -t 1 ] || return 1
  printf '%s [y/N]: ' "$1"
  IFS= read -r answer || return 1
  case $answer in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}
