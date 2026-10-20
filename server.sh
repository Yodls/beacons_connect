#!/usr/bin/env bash
# server.sh - manage the beacons_connect gunicorn server
# Usage: beacon {start|stop|restart|status|logs [N]|follow|version|update [TAG|URL]}

set -u

APP_DIR="$HOME/app/beacons_connect"
VENV="$APP_DIR/venv"
BIND="0.0.0.0:80"
APP_MODULE="app:app"
LOG_FILE="$APP_DIR/app.log"
PID_FILE="$APP_DIR/gunicorn.pid"
PROD_BRANCH="production"

# --- concurrency ------------------------------------------------------------
# The app long-polls: every open page holds one request for up to 25 seconds
# (LIVE_WAIT in app.py) and re-issues it the moment it returns, so one open tab
# occupies one request slot essentially all of the time.
#
# That makes the worker model load-bearing. The default is a single `sync`
# worker, which serves exactly ONE request at a time -- so a single open tab
# held the whole site and every click waited ~25s behind it.
#
# The rule: WORKERS x THREADS must comfortably exceed the number of tabs open
# at once. Do not lower these without lowering that expectation. Raising
# LIVE_WAIT's twin -- shortening the hold -- does NOT help: the client
# re-issues immediately, so the slot stays occupied either way.
WORKER_CLASS="gthread"   # threaded; a held request costs a thread, not a process
WORKERS=2
THREADS=32               # 2 x 32 = 64 concurrent requests
TIMEOUT=60               # must exceed the 25s hold; gunicorn's default is 30

# Set to 1 to log every request with its duration. Useful when something is
# slow and you cannot tell what; noisy otherwise, because each open tab adds a
# line every 25 seconds.
ACCESS_LOG=0

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

# Ask a yes/no question; Enter counts as yes
confirm() {
  local reply
  read -r -p "$1 [Y/n] " reply
  [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

start() {
  if is_running; then
    echo "Already running (PID $(cat "$PID_FILE"))"
    return 0
  fi

  cd "$APP_DIR" || { echo "Cannot cd to $APP_DIR"; exit 1; }

  local extra=()
  if [[ "$ACCESS_LOG" == "1" ]]; then
    # %(D)s is the request time in microseconds, which is the number you want
    # when hunting a slow page.
    extra+=(--access-logfile - --access-logformat \
            '%(h)s "%(r)s" %(s)s %(b)s %(D)sus')
  fi

  # Calling the venv's gunicorn directly is the same as activating the venv first
  nohup "$VENV/bin/gunicorn" -b "$BIND" \
    --worker-class "$WORKER_CLASS" \
    --workers "$WORKERS" \
    --threads "$THREADS" \
    --timeout "$TIMEOUT" \
    --graceful-timeout 30 \
    "${extra[@]+"${extra[@]}"}" \
    --pid "$PID_FILE" "$APP_MODULE" \
    >> "$LOG_FILE" 2>&1 &

  sleep 2
  if is_running; then
    echo "Started (PID $(cat "$PID_FILE")) -- $WORKERS worker(s) x $THREADS threads"
  else
    echo "Failed to start. Last log lines:"
    tail -n 20 "$LOG_FILE"
    return 1
  fi
}

stop() {
  if ! is_running; then
    # Catch instances started the old way (without a PID file)
    if pgrep -f "gunicorn.*$APP_MODULE" > /dev/null; then
      echo "No PID file, but found gunicorn running; stopping it"
      pkill -f "gunicorn.*$APP_MODULE"
      sleep 2
    else
      echo "Server is not running"
    fi
    rm -f "$PID_FILE"
    return 0
  fi

  local pid
  pid=$(cat "$PID_FILE")
  kill "$pid"

  # Give gunicorn up to 10 seconds to shut down gracefully
  for _ in {1..10}; do
    is_running || break
    sleep 1
  done

  if is_running; then
    echo "Didn't stop gracefully, forcing..."
    kill -9 "$pid"
  fi

  rm -f "$PID_FILE"
  echo "Stopped"
}

version() {
  cd "$APP_DIR" || exit 1
  git describe --tags --always 2>/dev/null || echo "unknown"
}

# List tags on the production branch, newest first,
# with pre-releases (v1.0.0-alpha.1) sorted before v1.0.0
sorted_tags() {
  git -c versionsort.suffix=- tag --sort=-v:refname --merged "origin/$PROD_BRANCH"
}

install_requirements_if_changed() {
  # $1 = old commit, $2 = new commit
  if ! git diff --quiet "$1" "$2" -- requirements.txt; then
    echo "requirements.txt changed, installing dependencies..."
    "$VENV/bin/pip" install -q -r requirements.txt
  fi
}

update() {
  cd "$APP_DIR" || { echo "Cannot cd to $APP_DIR"; exit 1; }

  # Refuse to update over local edits to tracked files
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Local changes found in tracked files. Commit or stash them first:"
    git status --short --untracked-files=no
    exit 1
  fi

  # Fetch tags plus the production branch, so origin/production is current
  # even if this server was cloned with only main
  echo "Fetching from GitHub..."
  git fetch --tags --quiet origin "+refs/heads/$PROD_BRANCH:refs/remotes/origin/$PROD_BRANCH" \
    || { echo "git fetch failed"; exit 1; }

  # Accept a plain tag or a full GitHub release URL (keeps the part after the last /)
  local target="${1:-}"
  target="${target##*/}"
  if [[ -z "$target" ]]; then
    target=$(sorted_tags | head -n 1)
    if [[ -z "$target" ]]; then
      echo "No tags found on the $PROD_BRANCH branch"
      exit 1
    fi
    echo "No tag given, using newest on $PROD_BRANCH: $target"
  fi

  if ! git rev-parse -q --verify "refs/tags/$target" > /dev/null; then
    echo "Tag '$target' not found. Recent tags on $PROD_BRANCH:"
    sorted_tags | head -n 10
    exit 1
  fi

  # Warn if a tag given by hand isn't part of the production branch
  if ! git merge-base --is-ancestor "$target" "origin/$PROD_BRANCH"; then
    echo "Warning: $target is not on the $PROD_BRANCH branch."
    confirm "Deploy it anyway?" || exit 1
  fi

  local prev prev_name
  prev=$(git rev-parse HEAD)
  prev_name=$(version)

  if [[ "$(git rev-parse "$target^{commit}")" == "$prev" ]]; then
    echo "Already on $target, nothing to update"
    return 0
  fi

  echo "Updating $prev_name -> $target"

  stop

  git checkout --quiet "$target" || { echo "Checkout failed, still on $prev_name"; exit 1; }

  if ! install_requirements_if_changed "$prev" HEAD; then
    echo "pip install failed, going back to $prev_name"
    git checkout --quiet "$prev"
    echo "Server is stopped. Run 'beacon start' to start $prev_name."
    exit 1
  fi

  echo "Update complete: now on $target"

  if ! confirm "Start the server now?"; then
    echo "Server left stopped. Run 'beacon start' when ready."
    return 0
  fi

  if ! start; then
    if confirm "$target failed to start. Roll back to $prev_name and start that?"; then
      git checkout --quiet "$prev"
      install_requirements_if_changed HEAD@{1} HEAD
      start
    fi
    exit 1
  fi
}

status() {
  if is_running; then
    echo "Running (PID $(cat "$PID_FILE")), version $(version)"
  else
    echo "Not running, version $(version)"
  fi
  echo "--- gunicorn processes ---"
  # The full command line is shown, so you can confirm the worker class and
  # thread count that are actually in effect rather than the ones intended.
  pgrep -af gunicorn || echo "(none)"
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    tail -n "${2:-50}" "$LOG_FILE" ;;
  follow)  tail -f "$LOG_FILE" ;;
  version) version ;;
  update)  update "${2:-}" ;;
  *)
    echo "Usage: $(basename "$0") {start|stop|restart|status|logs [N]|follow|version|update [TAG|URL]}"
    exit 1
    ;;
esac
