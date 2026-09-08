#!/bin/sh
# Python-free product prerequisite setup. Called by ./openrtl, never sourced.
# The checkout is trusted executable code. Installed runtimes are owner-private;
# they are not an attestation against modification by that same owner.
set -eu
umask 077
PATH=/usr/bin:/bin
export PATH
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
fail() { printf 'OpenRTL runtime setup stopped: %s\n' "$1" >&2; exit 2; }
kind=python
consent=no
offline=no
artifacts=
state=
interactive=no
[ "$#" -ne 0 ] || interactive=yes
while [ "$#" -gt 0 ]; do
    case "$1" in
        --prepare-uv) kind=uv ;;
        --allow-runtime-install) consent=yes ;;
        --offline) offline=yes ;;
        --runtime-artifacts|--state-dir)
            option=$1; shift
            [ "$#" -gt 0 ] || fail missing_option_value
            case "$option" in --runtime-artifacts) artifacts=$1 ;; --state-dir) state=$1 ;; esac ;;
    esac
    shift
done
platform=$(uname -s)-$(uname -m)
case "$platform" in
    Darwin-arm64)
        target=aarch64-apple-darwin
        python_target=cpython-3.13.15-macos-aarch64-none
        uv_sha=546f7f8a6c70ff13a3a9d2bc958db3427298cebf3e0cb756f9177133b7068843
        python_sha=dbadb0ffe46f8bace50daaf8a0c5fc6903c003690776da9eb5269e33c856bb53
        [ -n "$state" ] || state=${HOME:?}/Library/Application\ Support/OpenRTL ;;
    Linux-x86_64)
        target=x86_64-unknown-linux-gnu
        python_target=cpython-3.13.15-linux-x86_64-gnu
        uv_sha=600cf9a742aca00d292673b16b5acffaa7b8c269a364ad0c2e79498dcb1fe101
        python_sha=faae10a9faa9bec06da009ac69326cc1d9691dc138fec6a1b69159dff1781f35
        getconf GNU_LIBC_VERSION >/dev/null 2>&1 || fail glibc_required
        [ -n "$state" ] || state=${XDG_STATE_HOME:-${HOME:?}/.local/state}/openrtl ;;
    *) fail unsupported_platform_select_existing_python ;;
esac
case "$state" in /*) ;; *) fail state_directory_must_be_absolute ;; esac
# Walk without following links. Public ancestors are allowed only when they
# cannot be renamed by other users (root-owned sticky temporary roots included).
safe_path() (
    path=$1
    case "$path" in /*) ;; *) fail path_must_be_absolute ;; esac
    case "$path" in *'//'*) fail invalid_path ;; esac
    while [ "$path" != / ]; do
        case "${path##*/}" in ''|.|..) fail invalid_path ;; esac
        [ ! -L "$path" ] || fail linked_path
        if [ -e "$path" ]; then
            [ -d "$path" ] || fail directory_required
            if [ "$platform" = Darwin-arm64 ]; then
                metadata=$(stat -f '%u %p' "$path")
            else
                metadata=$(stat -c '%u %a' "$path")
            fi
            set -- $metadata
            owner=$1; mode=$2
            [ "$owner" = "$(id -u)" ] || [ "$owner" = 0 ] || fail untrusted_directory_owner
            # Accept an explicitly sticky system temp ancestor, never state.
            if [ $((0$mode & 0022)) -ne 0 ]; then
                [ "$owner" = 0 ] && [ $((0$mode & 01000)) -ne 0 ] || fail writable_ancestor
            fi
        fi
        path=${path%/*}; [ -n "$path" ] || path=/
    done
)
private_dir() {
    safe_path "$1"
    [ -d "$1" ] || return 1
    if [ "$platform" = Darwin-arm64 ]; then meta=$(stat -f '%u %Lp' "$1"); else meta=$(stat -c '%u %a' "$1"); fi
    set -- $meta
    [ "$1" = "$(id -u)" ] && [ $((0$2 & 0077)) -eq 0 ] || fail private_owned_directory_required
}
private_file() {
    [ -f "$1" ] && [ ! -L "$1" ] || fail private_regular_file_required
    if [ "$platform" = Darwin-arm64 ]; then meta=$(stat -f '%u %Lp %l %z' "$1"); else meta=$(stat -c '%u %a %h %s' "$1"); fi
    set -- $meta
    [ "$1" = "$(id -u)" ] && [ $((0$2 & 0077)) -eq 0 ] && [ "$3" = 1 ] && [ "$4" -le 8192 ] || fail invalid_runtime_receipt
}
safe_path "$state"
base=$state/runtime/uv-0.12.3-$target
active=$base/python-active
if [ -e "$state" ]; then private_dir "$state" || fail private_state_required; fi
if [ -e "$state/runtime" ]; then private_dir "$state/runtime" || fail private_runtime_required; fi
if [ -e "$base" ]; then
    private_dir "$base" || fail private_runtime_required
    if [ "$kind" = python ] && { [ -e "$active" ] || [ -L "$active" ]; }; then
        private_file "$active"
        attempt=$(cat "$active")
        case "$attempt" in python.??????????) ;; *) fail invalid_runtime_receipt ;; esac
        case "${attempt#python.}" in *[!a-zA-Z0-9]*) fail invalid_runtime_receipt ;; esac
        private_dir "$base/$attempt" || fail missing_runtime_attempt
        selected=$base/$attempt/install/$python_target/bin/python3.13
        private_file "$base/$attempt/complete"
        safe_path "${selected%/*}"
        [ ! -L "$selected" ] && [ -x "$selected" ] || fail invalid_python_executable
        printf '%s\n' "$selected"
        exit 0
    fi
fi
uv_file=uv-$target.tar.gz
python_file=cpython-3.13.15+20260807-$target-install_only_stripped.tar.gz
printf '%s\n' "OpenRTL private setup: uv 0.12.3 ($target); CPython 3.13.15 build 20260807 when Python is needed." \
    'Archives are hash-checked; download limits are 64 MiB for uv and 128 MiB for Python.' \
    'Only private OpenRTL files are prepared. System Python, shell profiles and Docker are unchanged.' >&2
if [ "$consent" != yes ]; then
    if [ "$interactive" = yes ] && [ -t 0 ] && [ -t 2 ]; then
        printf '%s' 'Approve these pinned runtime downloads and installation? [y/N] ' >&2
        read -r answer || answer=n
        [ "$answer" != y ] || consent=yes
    fi
fi
[ "$consent" = yes ] || fail 'runtime_consent_required; use --allow-runtime-install, or select OPENRTL_PYTHON'
if [ "$offline" = yes ] && [ -z "$artifacts" ]; then fail offline_artifacts_required; fi
if [ -n "$artifacts" ]; then safe_path "$artifacts"; [ -d "$artifacts" ] || fail artifacts_directory_missing; fi
mkdir -p -- "$base"
private_dir "$state" && private_dir "$state/runtime" && private_dir "$base" || fail private_storage_required
# Atomic mkdir serializes setup. On SIGKILL the empty lock is deliberately kept;
# another process is never killed or unlocked by a guessed PID.
mkdir -- "$base/setup.lock" 2>/dev/null || fail 'setup_locked; if no setup is running, remove only the empty runtime setup.lock directory'
trap 'rmdir -- "$base/setup.lock"' 0
trap 'exit 130' INT TERM HUP
hash_file() {
    [ -f "$1" ] && [ ! -L "$1" ] || fail artifact_file_required
    if [ "$platform" = Darwin-arm64 ]; then
        value=$(env -i PATH=/usr/bin:/bin LANG=C LC_ALL=C shasum -a 256 "$1" 2>/dev/null) || fail sha256_tool_unavailable
    else
        value=$(env -i PATH=/usr/bin:/bin LANG=C LC_ALL=C sha256sum "$1" 2>/dev/null) || fail sha256_tool_unavailable
    fi
    [ "${value%% *}" = "$2" ] || fail artifact_hash_mismatch
}
fetch() {
    filename=$1; digest=$2; url=$3; bound=$4
    if [ ! -e "$base/$filename" ] && [ ! -L "$base/$filename" ]; then
        partial=$(mktemp "$base/artifact.XXXXXXXXXX")
        if [ -n "$artifacts" ]; then
            [ -f "$artifacts/$filename" ] && [ ! -L "$artifacts/$filename" ] || fail offline_artifact_missing
            if [ "$platform" = Darwin-arm64 ]; then links=$(stat -f '%l' "$artifacts/$filename"); else links=$(stat -c '%h' "$artifacts/$filename"); fi
            [ "$links" = 1 ] || fail linked_artifact_rejected
            size=$(wc -c < "$artifacts/$filename")
            [ "$size" -le "$bound" ] || fail artifact_size_limit
            cp -- "$artifacts/$filename" "$partial"
        else
            [ "$offline" = no ] || fail offline_artifact_missing
            # -q is first: no curlrc. No inherited proxy, auth, cookie or netrc.
            env -i PATH=/usr/bin:/bin LANG=C LC_ALL=C /usr/bin/curl -q --fail --silent \
                --location --max-redirs 3 --proto '=https' --proto-redir '=https' \
                --connect-timeout 15 --max-time 120 --max-filesize "$bound" \
                --output "$partial" "$url" || fail download_unavailable
        fi
        hash_file "$partial" "$digest"
        mv -- "$partial" "$base/$filename"
    fi
    hash_file "$base/$filename" "$digest"
}
fetch "$uv_file" "$uv_sha" "https://github.com/astral-sh/uv/releases/download/0.12.3/$uv_file" 67108864
# Extract only the single executable from an authenticated archive. No installer
# script, package hooks or arbitrary archive paths are run.
uv_attempt=$(mktemp -d "$base/tool.XXXXXXXXXX")
tar -xzOf "$base/$uv_file" "uv-$target/uv" > "$uv_attempt/uv" || fail uv_archive_invalid
chmod 700 "$uv_attempt/uv"
uv=$uv_attempt/uv
case "$(env -i PATH=/usr/bin:/bin "$uv" --version)" in 'uv 0.12.3'|'uv 0.12.3 ('*) ;; *) fail uv_version_mismatch ;; esac
if [ "$kind" = uv ]; then printf '%s\n' "$uv"; exit 0; fi
fetch "$python_file" "$python_sha" "https://github.com/astral-sh/python-build-standalone/releases/download/20260807/cpython-3.13.15%2B20260807-$target-install_only_stripped.tar.gz" 134217728
attempt_path=$(mktemp -d "$base/python.XXXXXXXXXX")
mkdir "$attempt_path/home" "$attempt_path/cache" "$attempt_path/mirror" "$attempt_path/mirror/20260807"
cp -- "$base/$python_file" "$attempt_path/mirror/20260807/$python_file"
# file:// mirrors support spaces; encode characters with URL significance.
case "$attempt_path" in *'%'*|*'#'*|*'?'*|*'\'* ) fail state_path_not_supported_for_runtime_mirror ;; esac
mirror=$(printf '%s' "$attempt_path/mirror" | sed 's/ /%20/g')
# uv owns its extraction/relocation. Networking is impossible in this action:
# only the previously hashed local mirror can supply the pinned frozen catalog entry.
env -i PATH=/usr/bin:/bin HOME="$attempt_path/home" LANG=C LC_ALL=C \
    UV_PYTHON_CPYTHON_BUILD=20260807 UV_PYTHON_INSTALL_BIN=0 \
    "$uv" --no-config --offline --cache-dir "$attempt_path/cache" python install \
    --install-dir "$attempt_path/install" --no-bin --mirror "file://$mirror" \
    "$python_target" >/dev/null 2>&1 &
child=$!
trap 'kill -KILL "$child" 2>/dev/null || :; wait "$child" 2>/dev/null || :; exit 130' INT TERM HUP
# Bound this exact child, with no detached watchdog or persistent process.
elapsed=0
while kill -0 "$child" 2>/dev/null; do
    if [ "$elapsed" -ge 120 ]; then
        kill -KILL "$child" 2>/dev/null || :
        wait "$child" 2>/dev/null || :
        fail python_install_deadline
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done
result=0
wait "$child" || result=$?
trap 'exit 130' INT TERM HUP
[ "$result" -eq 0 ] || fail python_install_failed_attempt_retained
selected=$attempt_path/install/$python_target/bin/python3.13
"$selected" -I -S -c 'import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 13, 15) else 2)' >/dev/null 2>&1 || fail python_self_check_failed
printf '%s\n' 'cpython-3.13.15+20260807; local import check only; M46/M47 pending' > "$attempt_path/complete"
printf '%s\n' "${attempt_path##*/}" > "$attempt_path/active-next"
[ ! -e "$active" ] && [ ! -L "$active" ] || fail runtime_destination_changed
mv -- "$attempt_path/active-next" "$active"
printf '%s\n' "$selected"
