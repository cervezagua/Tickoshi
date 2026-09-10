#!/usr/bin/env bash
# ==========================================================
#   Tickoshi Live Bitcoin Price Widget — Linux Build Script
#   Produces: dist/Tickoshi  (single-file ELF binary)
# ==========================================================
set -e

echo ""
echo " ==========================================="
echo " Tickoshi Live Bitcoin Price Widget- Builder"
echo " ==========================================="
echo ""

# ── Python check ──────────────────────────────────────────
if ! python3 --version &>/dev/null; then
    echo " [ERROR] python3 not found. Install it from your package manager."
    echo "         Ubuntu/Debian: sudo apt install python3 python3-pip python3-tk"
    exit 1
fi

if ! python3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)"; then
    echo " [ERROR] Python 3.10+ required."
    exit 1
fi

# Tkinter check (must be installed separately on most Linux distros)
if ! python3 -c "import tkinter" &>/dev/null; then
    echo " [ERROR] python3-tk not found."
    echo "         Ubuntu/Debian: sudo apt install python3-tk"
    echo "         Fedora:        sudo dnf install python3-tkinter"
    echo "         Arch:          sudo pacman -S tk"
    exit 1
fi

if [ ! -f "Tickoshi.py" ]; then
    echo " [ERROR] Tickoshi.py not found in current directory."
    exit 1
fi

# ── Kill existing instances ───────────────────────────────
pkill -f "Tickoshi.py" 2>/dev/null || true
pkill -f "Tickoshi"     2>/dev/null || true
echo ""

# ── Dependencies ──────────────────────────────────────────
echo " [1/3] Checking dependencies..."
# Via `python3 -m` throughout: a pip --user install puts the console script
# in ~/.local/bin, which is not on PATH in every shell.
python3 -m pip show pyinstaller      &>/dev/null || python3 -m pip install "pyinstaller>=6.0"
python3 -m pip show pillow           &>/dev/null || python3 -m pip install "pillow>=10.0"
python3 -m pip show websocket-client &>/dev/null || python3 -m pip install "websocket-client>=1.6"

# ── Clean ─────────────────────────────────────────────────
echo " [2/3] Cleaning previous build..."
rm -rf build dist

# ── Build ─────────────────────────────────────────────────
echo " [3/3] Building binary..."
echo ""

python3 -m PyInstaller \
  --onefile \
  --windowed \
  --name "Tickoshi" \
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
Icon=$SCRIPT_DIR/Tickoshi.png
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
