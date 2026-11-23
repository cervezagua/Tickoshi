#!/usr/bin/env bash
# ==========================================================
#   Tickoshi Live Bitcoin Price Widget — Linux Build Script
#   Produces: dist/Tickoshi  (single-file ELF binary)
# ==========================================================
set -e

# Run from the script's own folder, so it also works when started from
# elsewhere or double-clicked in a file manager.
cd "$(dirname "$0")"

echo ""
echo " ==========================================="
echo " Tickoshi Live Bitcoin Price Widget- Builder"
echo " ==========================================="
echo ""

if [ ! -f "Tickoshi.py" ]; then
    echo " [ERROR] Tickoshi.py not found next to this script."
    exit 1
fi

# ── System packages ───────────────────────────────────────
# The build needs Python 3.10+ with tkinter and venv, plus objdump
# (binutils), which PyInstaller uses on Linux. Debian, Ubuntu and Kali ship
# tkinter and venv as separate packages. Anything missing is offered for
# install with the system package manager — asked first, never silently.
ask() {  # ask "question" -> 0 for yes (the default), 1 for no or no terminal
    local a
    [ -t 0 ] || return 1
    read -r -p "$1 [Y/n] " a || return 1
    case "$a" in [nN]*) return 1;; esac
    return 0
}

# Sets MISSING (packages to install) and INSTALL (the command to install
# them, empty if no known package manager). Not run in a subshell: both
# must survive the call.
missing_packages() {
    local py_ok=0 pkgs=""
    if command -v python3 >/dev/null 2>&1 &&
       python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        py_ok=1
    fi
    if command -v apt-get >/dev/null 2>&1; then
        [ $py_ok = 1 ] || pkgs="$pkgs python3"
        python3 -c 'import tkinter' 2>/dev/null || pkgs="$pkgs python3-tk"
        python3 -c 'import ensurepip, venv' 2>/dev/null || pkgs="$pkgs python3-venv"
        command -v objdump >/dev/null 2>&1 || pkgs="$pkgs binutils"
        INSTALL="sudo apt-get install -y"
    elif command -v dnf >/dev/null 2>&1; then
        [ $py_ok = 1 ] || pkgs="$pkgs python3"
        python3 -c 'import tkinter' 2>/dev/null || pkgs="$pkgs python3-tkinter"
        command -v objdump >/dev/null 2>&1 || pkgs="$pkgs binutils"
        INSTALL="sudo dnf install -y"
    elif command -v pacman >/dev/null 2>&1; then
        [ $py_ok = 1 ] || pkgs="$pkgs python"
        python3 -c 'import tkinter' 2>/dev/null || pkgs="$pkgs tk"
        command -v objdump >/dev/null 2>&1 || pkgs="$pkgs binutils"
        INSTALL="sudo pacman -S --needed --noconfirm"
    else
        INSTALL=""
        [ $py_ok = 1 ] || pkgs="$pkgs python3(3.10+)"
        python3 -c 'import tkinter' 2>/dev/null || pkgs="$pkgs tkinter"
        python3 -c 'import ensurepip, venv' 2>/dev/null || pkgs="$pkgs python3-venv"
        command -v objdump >/dev/null 2>&1 || pkgs="$pkgs binutils"
    fi
    MISSING="$pkgs"
}

missing_packages
if [ -n "$MISSING" ]; then
    echo " Missing for the build:$MISSING"
    if [ -n "$INSTALL" ] && ask " Install them now ($INSTALL$MISSING)? This asks for your password."; then
        $INSTALL $MISSING
        missing_packages
    fi
    if [ -n "$MISSING" ]; then
        echo ""
        echo " [ERROR] Still missing:$MISSING"
        echo "         Debian/Ubuntu/Kali: sudo apt install python3 python3-tk python3-venv binutils"
        echo "         Fedora:             sudo dnf install python3 python3-tkinter binutils"
        echo "         Arch:               sudo pacman -S python tk binutils"
        exit 1
    fi
fi
echo " Using $(command -v python3) ($(python3 --version 2>&1))"

# ── Kill existing instances ───────────────────────────────
# Only the built binary and a from-source run. A bare "Tickoshi" pattern
# also matched this script whenever its path contained the word.
pkill -f "dist/Tickoshi"  2>/dev/null || true
pkill -f "Tickoshi.py"    2>/dev/null || true
echo ""

# ── Dependencies ──────────────────────────────────────────
echo " [1/3] Checking dependencies..."
# Build in a private virtual environment. Debian, Ubuntu, Kali and other
# distros refuse `pip install` into the system Python
# ("externally-managed-environment", PEP 668), and a venv keeps PyInstaller
# and friends out of it anyway. Rebuilt whenever the system Python changes.
PY=$(command -v python3)
PY_ID="$PY $("$PY" --version 2>&1)"
VENV=".build-venv"
if [ ! -x "$VENV/bin/python" ] || [ "$(cat "$VENV/.base" 2>/dev/null)" != "$PY_ID" ]; then
    rm -rf "$VENV"
    "$PY" -m venv "$VENV"
    echo "$PY_ID" > "$VENV/.base"
fi
VPY="$VENV/bin/python"
# No `pip install --upgrade pip` here: Debian-family pip (Ubuntu, Kali)
# can crash replacing itself mid-run, and the bundled pip is new enough.
"$VPY" -m pip install --quiet "pyinstaller>=6.0" "pillow>=10.1" "websocket-client>=1.6"

# ── Clean ─────────────────────────────────────────────────
echo " [2/3] Cleaning previous build..."
rm -rf build dist

# ── Build ─────────────────────────────────────────────────
echo " [3/3] Building binary..."
echo ""

"$VPY" -m PyInstaller \
  --onefile \
  --windowed \
  --name "Tickoshi" \
  --hidden-import PIL._tkinter_finder \
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
  --exclude-module certifi \
  Tickoshi.py

# ── Result ────────────────────────────────────────────────
echo ""
if [ -f "dist/Tickoshi" ]; then
    chmod +x dist/Tickoshi
    SIZE=$(stat -c%s "dist/Tickoshi" 2>/dev/null || stat -f%z "dist/Tickoshi")
    echo " ========================================="
    echo "   SUCCESS!  dist/Tickoshi is ready"
    echo " ========================================="
    echo ""
    echo "   Size: $SIZE bytes"
    echo "   Run:  ./dist/Tickoshi"
    echo ""
    echo "   Config saved to: ~/.config/Tickoshi/"
    echo ""

    # ── Create .desktop launcher ──────────────────────────
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    DESKTOP_DIR="$HOME/.local/share/applications"
    mkdir -p "$DESKTOP_DIR"
    cat > "$DESKTOP_DIR/tickoshi.desktop" <<EOF
[Desktop Entry]
Name=Tickoshi
Comment=Live Bitcoin price widget
Exec=$SCRIPT_DIR/dist/Tickoshi
Icon=$SCRIPT_DIR/assets/Tickoshi.png
Type=Application
Categories=Utility;Finance;
StartupNotify=false
Terminal=false
EOF
    chmod +x "$DESKTOP_DIR/tickoshi.desktop"
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    echo "   Desktop launcher created: $DESKTOP_DIR/tickoshi.desktop"
    echo ""
else
    echo " [ERROR] Build failed. Check output above."
    exit 1
fi
