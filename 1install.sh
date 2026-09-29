#!/usr/bin/env bash
# wp-video-dl installer for a fresh Ubuntu server.
#
# Standalone deploy:
#   Put this file AND wp-video-dl.py in the same directory, then:
#       bash install.sh
#
# One-liner deploy with a hosted script:
#       curl -fsSL https://YOUR-HOST/install.sh -o install.sh \
#         && WPVIDL_SRC_URL=https://YOUR-HOST/wp-video-dl.py bash install.sh
#
# It copies wp-video-dl.py to ~/wp-video-dl/, makes a `wp-video-dl` command
# available, creates logs/ and downloads/ folders, then launches the
# interactive flow (you answer: target website -> month -> download?).
set -eu

APP_NAME="wp-video-dl"
SRC_URL="${WPVIDL_SRC_URL:-}"
# the two filenames that matter
local_py="wp-video-dl.py"
local_sh="install.sh"

# --- helpers ---------------------------------------------------------------
info()   { printf '\n\033[1;34m[setup] %s\033[0m\n' "$1"; }
ok()     { printf '\033[1;32m[ok] %s\033[0m\n' "$1"; }
warn()   { printf '\033[1;33m[warn] %s\033[0m\n' "$1"; }
die()    { printf '\033[1;31m[fatal] %s\033[0m\n' "$1" >&2; exit 1; }
need()   { command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"; }

info "wp-video-dl installer"

# --- prerequisites ---------------------------------------------------------
need curl
need python3
PY=$(command -v python3)
if ! "$PY" -c 'import sys; v=sys.version_info; sys.exit(0 if v>=(3,8) else 1)'; then
    die "python3 >= 3.8 required (found $("$PY" -V 2>&1 || true))"
fi

# --- decide where the script comes from ------------------------------------
have_local=0
[ -s "$local_py" ] && have_local=1
if [ "$have_local" -eq 0 ] && [ -z "$SRC_URL" ]; then
    die "no $local_py in this directory and no WPVIDL_SRC_URL given."
fi

# --- run as current user, install under ~ -----------------------------------
[ -n "${VIRTUAL_ENV:-}" ] && warn "should probably exit your venv before installing"
INST_DIR="$HOME/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
TARGET="$INST_DIR/$local_py"
LINK="$BIN_DIR/$APP_NAME"

mkdir -p "$INST_DIR" "$BIN_DIR" "$INST_DIR/logs" "$INST_DIR/downloads"

if [ "$have_local" -eq 1 ]; then
    info "using local $local_py"
    cp "$local_py" "$TARGET"
elif [ -n "$SRC_URL" ]; then
    info "downloading from $SRC_URL"
    curl -fsSL --fail-early --retry 3 "$SRC_URL" -o "$TARGET" \
        || die "failed to fetch script from $SRC_URL"
fi

chmod +x "$TARGET"

if ! "$PY" -m py_compile "$TARGET" 2>/dev/null; then
    die "$local_py failed to compile (syntax error). Aborting before any run."
fi
ok "installed $local_py ($("$PY" -c "print(open('$TARGET').read().count(chr(10))+1)") lines) to $TARGET"

# idempotent PATH entry for the wrapper
if ! grep -qF "export PATH=\$HOME/.local/bin" "$HOME/.bashrc" 2>/dev/null; then
    printf '\n# wp-video-dl\nexport PATH=$HOME/.local/bin:$PATH\n' >> "$HOME/.bashrc"
fi
ln -sf "$TARGET" "$LINK"
export PATH="$BIN_DIR:$PATH"
ok "command '$APP_NAME' is available (log in again to get it in all shells)"

[ -d "$INST_DIR/logs" ] && ok "logs -> $INST_DIR/logs"
ok "downloads will go to $INST_DIR/downloads"

# --- reveal env knobs once ---------------------------------------------------
cat <<EOF

${APP_NAME} is installed. Environment:
  WPVIDL_SITE    default target website  (optional; otherwise it asks)
  WPVIDL_OUT     default download folder (default $INST_DIR/downloads)
  WPVIDL_LOG_DIR where per-run logs live (default $INST_DIR/logs)

Type-check done. Next, this starts the interactive flow — it will ask for
the website, the month, show count + estimated GB, then confirm the download.
Press Ctrl-C to cancel at any point.
EOF

if [ -t 0 ]; then
    "$BIN_DIR/$APP_NAME" month
else
    warn "non-interactive shell (no TTY): run  $APP_NAME month  yourself."
    warn "one-off survey without download:  $APP_NAME survey 2026 8  (via $APP_NAME --site URL survey 2026 8)"
fi