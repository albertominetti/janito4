#!/bin/sh
#
# janito-container entrypoint.
#
# Starts the Janito web server headless:
#   - --web              web UI mode
#   - --no-web-open      never auto-open a browser (headless container)
#   - --web-host/--web-port   from $WEB_HOST / $WEB_PORT (defaults 0.0.0.0:8080)
#
# Extra CLI flags passed to `docker run ... janito <flags>` are forwarded, and
# an explicit --web-host/--web-port on the command line wins over the
# environment variables (no duplicate flags are emitted).
#
# Special mode: `--healthcheck` (used by the Docker HEALTHCHECK) probes the
# running server's /api/health endpoint with Python's stdlib and exits 0/1.
set -eu

WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-8080}"

if [ "${1:-}" = "--healthcheck" ]; then
    shift
    python -c 'import sys, urllib.request
try:
    status = urllib.request.urlopen(
        "http://127.0.0.1:%s/api/health" % sys.argv[1], timeout=3
    ).status
except Exception:
    sys.exit(1)
sys.exit(0 if status == 200 else 1)' "$WEB_PORT"
    exit $?
fi

have_flag() {
    opt="$1"
    shift
    for arg in "$@"; do
        [ "$arg" = "$opt" ] && return 0
    done
    return 1
}

server_args=""
if ! have_flag "--web-host" "$@"; then
    server_args="$server_args --web-host $WEB_HOST"
fi
if ! have_flag "--web-port" "$@"; then
    server_args="$server_args --web-port $WEB_PORT"
fi

# shellcheck disable=SC2086
exec janito --web --no-web-open $server_args "$@"