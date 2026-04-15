#!/bin/bash
# Supervisor wrapper for `claude-mem start`.
#
# claude-mem start spawns a Bun worker daemon and returns. systemd needs a
# long-running foreground process to supervise; this script starts the worker,
# then tails worker.pid so that systemd sees the service as alive for as long
# as the actual worker process is alive. When the worker dies (or vanishes),
# we exit 1 so that Restart=on-failure kicks in.
set -eu

# Resolve node/bun paths at start time so nvm upgrades don't require unit edits.
nvm_bin=$(ls -d "$HOME"/.nvm/versions/node/v* 2>/dev/null | sort -V | tail -n1)/bin
export PATH="$HOME/.bun/bin:$nvm_bin:$HOME/.local/share/pnpm:/usr/local/bin:/usr/bin:/bin"

if ! command -v npx >/dev/null; then
    echo "npx not found in PATH=$PATH" >&2
    exit 1
fi

# `claude-mem start` is idempotent: if a worker already runs, it no-ops.
npx -y claude-mem start

pid_file="$HOME/.claude-mem/worker.pid"
# Give it up to 5s to materialize the pid file.
for _ in 1 2 3 4 5; do
    [ -f "$pid_file" ] && break
    sleep 1
done

if [ ! -f "$pid_file" ]; then
    echo "worker.pid never appeared at $pid_file" >&2
    exit 1
fi

# Extract pid via sed — avoids adding a Python dep on the hot path.
pid=$(sed -n 's/.*"pid"[[:space:]]*:[[:space:]]*\([0-9]\+\).*/\1/p' "$pid_file" | head -n1)
if [ -z "$pid" ]; then
    echo "failed to parse pid from $pid_file (contents: $(cat "$pid_file"))" >&2
    exit 1
fi

echo "supervising worker pid=$pid"

# Handle graceful shutdown: forward SIGTERM to the worker.
trap 'kill -TERM "$pid" 2>/dev/null || true; exit 0' TERM INT

while kill -0 "$pid" 2>/dev/null; do
    sleep 10
done

echo "worker $pid exited; triggering service restart" >&2
exit 1
