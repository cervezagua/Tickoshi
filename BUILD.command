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
if ! python3 --version &>/dev/null; then
    echo " [ERROR] python3 not found."
    echo "         Install from python.org or: brew install python"
    exit 1
fi

if ! python3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)"; then
    echo " [ERROR] Python 3.10+ required."
    exit 1
fi

# Tkinter ships with the python.org installer and Homebrew python, but
# system Python on older macOS sometimes lacks it — warn early.
if ! python3 -c "import tkinter" &>/dev/null; then
    echo " [ERROR] tkinter not available in this Python."
    echo "         Install Python from python.org (bundles Tk) or:"
    echo "           brew install python-tk"
    exit 1
fi

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
# Everything goes through `python3 -m` — on Macs with several Pythons
# (Homebrew, python.org, Xcode CLT), bare `pip3` can belong to a different
# interpreter than `python3`, and --user console scripts land in
# ~/Library/Python/3.x/bin which is not on PATH.
python3 -m pip show pyinstaller      &>/dev/null || python3 -m pip install --user "pyinstaller>=6.0"
python3 -m pip show pillow           &>/dev/null || python3 -m pip install --user "pillow>=10.0"
python3 -m pip show websocket-client &>/dev/null || python3 -m pip install --user "websocket-client>=1.6"
# certifi supplies the CA bundle the app falls back to on macOS, where
# Python's OpenSSL can't read the system Keychain (see Tickoshi.py header).
python3 -m pip show certifi          &>/dev/null || python3 -m pip install --user "certifi"

# ── Clean ─────────────────────────────────────────────────
echo " [2/4] Cleaning previous build..."
rm -rf build dist

# ── Build ─────────────────────────────────────────────────
echo " [3/4] Building .app bundle..."
echo ""

ICON_ARG=()
if [ -f "Tickoshi.icns" ]; then
    ICON_ARG=(--icon "Tickoshi.icns")
fi

python3 -m PyInstaller \
  --onefile \
  --windowed \
  --name "Tickoshi" \
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
