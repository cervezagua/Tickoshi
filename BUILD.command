#!/usr/bin/env bash
# ==========================================================
#   Tickoshi Live Bitcoin Price Widget — macOS Build Script
#   Produces: dist/Tickoshi.app  (ad-hoc signed bundle)
#            dist/Tickoshi-macos.zip  (release-upload archive)
# ==========================================================
#
# Double-clickable from Finder (hence the .command extension).
# If Finder refuses, right-click → Open, or:
#     chmod +x BUILD.command && ./BUILD.command
set -e

# Hop to the script's own directory — .command files launch from $HOME
# by default when double-clicked from Finder.
cd "$(dirname "$0")"

echo ""
echo " ==========================================="
echo " Tickoshi Live Bitcoin Price Widget- Builder"
echo " ==========================================="
echo ""

# ── Python check ──────────────────────────────────────────
# Macs usually have several Pythons, and the first `python3` on PATH is
# often Apple's own /usr/bin/python3 (3.9 on current macOS), which is too
# old. So look for one that is 3.10+ and has tkinter, newest first, in the
# places python.org and Homebrew install to. Set PYTHON=/path/to/python3 to
# choose one yourself.
PY=""
NO_TK=""
pick_python() {
    for c in ${PYTHON:+"$PYTHON"} \
             python3.14 python3.13 python3.12 python3.11 python3.10 \
             /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
             /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
        p=$(command -v "$c" 2>/dev/null) || continue
        "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
            2>/dev/null || continue
        if "$p" -c 'import tkinter' 2>/dev/null; then
            PY="$p"
            return 0
        fi
        [ -n "$NO_TK" ] || NO_TK="$p"
    done
    return 1
}

if ! pick_python; then
    if [ -n "$NO_TK" ]; then
        echo " [ERROR] Found Python 3.10+ at $NO_TK, but it has no tkinter."
        echo "         Homebrew Python needs its Tk package, matching the version:"
        echo "           brew install python-tk@$("$NO_TK" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
        echo "         Or install Python from python.org, which includes Tk."
    else
        CUR=$(command -v python3 2>/dev/null || true)
        echo " [ERROR] Python 3.10+ required."
        if [ -n "$CUR" ]; then
            echo "         The python3 found was $CUR ($("$CUR" --version 2>&1))."
        fi
        echo "         macOS's built-in python3 is too old. Install a current Python:"
        echo "           https://www.python.org/downloads/macos/   (includes Tk)"
        echo "         or with Homebrew:"
        echo "           brew install python@3.12 python-tk@3.12"
        echo "         then run this script again."
    fi
    exit 1
fi
echo " Using $PY ($("$PY" --version 2>&1))"

if [ ! -f "Tickoshi.py" ]; then
    echo " [ERROR] Tickoshi.py not found in current directory."
    exit 1
fi

# ── Kill existing instance ────────────────────────────────
pkill -f "Tickoshi.app"  2>/dev/null || true
pkill -f "Tickoshi.py"   2>/dev/null || true
echo ""

# ── Dependencies ──────────────────────────────────────────
echo " [1/4] Checking dependencies..."
# Build in a private virtual environment. Homebrew's Python refuses
# `pip install` outside one ("externally-managed-environment"), and a venv
# also keeps PyInstaller and friends out of the user's own Python. It is
# rebuilt whenever the Python chosen above changes.
VENV=".build-venv"
if [ ! -x "$VENV/bin/python" ] || [ "$(cat "$VENV/.base" 2>/dev/null)" != "$PY" ]; then
    rm -rf "$VENV"
    "$PY" -m venv "$VENV"
    echo "$PY" > "$VENV/.base"
fi
VPY="$VENV/bin/python"
"$VPY" -m pip install --quiet --upgrade pip
# certifi supplies the CA bundle the app falls back to on macOS, where
# Python's OpenSSL can't read the system Keychain (see Tickoshi.py header).
"$VPY" -m pip install --quiet "pyinstaller>=6.0" "pillow>=10.1" \
    "websocket-client>=1.6" certifi

# ── Clean ─────────────────────────────────────────────────
echo " [2/4] Cleaning previous build..."
rm -rf build dist

# ── Build ─────────────────────────────────────────────────
echo " [3/4] Building .app bundle..."
echo ""

ICON_ARG=()
if [ -f "assets/Tickoshi.icns" ]; then
    ICON_ARG=(--icon "assets/Tickoshi.icns")
fi

"$VPY" -m PyInstaller \
  --onefile \
  --windowed \
  --name "Tickoshi" \
  --hidden-import PIL._tkinter_finder \
  "${ICON_ARG[@]}" \
  --exclude-module numpy \
  --exclude-module pandas \
  --exclude-module matplotlib \
  --exclude-module scipy \
  --exclude-module IPython \
  --exclude-module notebook \
  --exclude-module docutils \
  --exclude-module setuptools \
  --exclude-module pkg_resources \
  --exclude-module xmlrpc \
  --exclude-module unittest \
  --exclude-module http.server \
  --exclude-module ftplib \
  --exclude-module imaplib \
  --exclude-module poplib \
  --exclude-module smtplib \
  --exclude-module telnetlib \
  Tickoshi.py

# ── Ad-hoc sign ───────────────────────────────────────────
# Free, no Apple Developer ID required. Fixes the "app is damaged and
# can't be opened" error on Apple Silicon. Gatekeeper will still show
# the "unidentified developer" warning on first launch — users need to
# right-click → Open, or run:
#     xattr -d com.apple.quarantine dist/Tickoshi.app
echo ""
echo " [4/4] Ad-hoc signing..."
if [ -d "dist/Tickoshi.app" ]; then
    codesign --force --deep --sign - "dist/Tickoshi.app"
fi

# ── Result ────────────────────────────────────────────────
echo ""
if [ -d "dist/Tickoshi.app" ]; then
    SIZE=$(du -sh "dist/Tickoshi.app" | cut -f1)

    # Zip for release upload (GitHub release assets can't preserve .app
    # bundle structure raw; ditto -k keeps resource forks intact).
    ditto -c -k --keepParent "dist/Tickoshi.app" "dist/Tickoshi-macos.zip"
    ZIP_SIZE=$(du -sh "dist/Tickoshi-macos.zip" | cut -f1)

    echo " ========================================="
    echo "   SUCCESS!  dist/Tickoshi.app is ready"
    echo " ========================================="
    echo ""
    echo "   App bundle: dist/Tickoshi.app  ($SIZE)"
    echo "   Zip:        dist/Tickoshi-macos.zip  ($ZIP_SIZE)"
    echo "   Run:        open dist/Tickoshi.app"
    echo ""
    echo "   Config saved to: ~/Library/Application Support/Tickoshi/"
    echo ""
    echo "   First launch (Gatekeeper):"
    echo "     Right-click the app → Open, or run:"
    echo "       xattr -d com.apple.quarantine dist/Tickoshi.app"
    echo ""
else
    echo " [ERROR] Build failed. Check output above."
    exit 1
fi
