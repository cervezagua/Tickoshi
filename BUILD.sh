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

# Tkinter check (must be installed separately on most Linux distros)
if ! python3 -c "import tkinter" &>/dev/null; then
    echo " [ERROR] python3-tk not found."
    echo "         Ubuntu/Debian: sudo apt install python3-tk"
    echo "         Fedora:        sudo dnf install python3-tkinter"
    echo "         Arch:          sudo pacman -S tk"
    exit 1
fi

# ── Kill existing instances ───────────────────────────────
pkill -f "Tickoshi.py" 2>/dev/null || true
pkill -f "Tickoshi"     2>/dev/null || true
echo ""

# ── Dependencies ──────────────────────────────────────────
echo " [1/3] Checking dependencies..."
pip3 show pyinstaller &>/dev/null || pip3 install pyinstaller
pip3 show pillow       &>/dev/null || pip3 install pillow

# ── Clean ─────────────────────────────────────────────────
echo " [2/3] Cleaning previous build..."
rm -rf build dist

# ── Build ─────────────────────────────────────────────────
echo " [3/3] Building binary..."
echo ""

pyinstaller \
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
  --exclude-module xml \
  --exclude-module xmlrpc \
  --exclude-module unittest \
  --exclude-module http.server \
  --exclude-module ftplib \
  --exclude-module imaplib \
  --exclude-module poplib \
  --exclude-module smtplib \
  --exclude-module telnetlib \
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

    # Optional: create a .desktop launcher
    DESKTOP_DIR="$HOME/.local/share/applications"
    BINARY_PATH="$(pwd)/dist/Tickoshi"
    ICON_PATH="$(pwd)/Tickoshi.ico"
    mkdir -p "$DESKTOP_DIR"
    cat > "$DESKTOP_DIR/Tickoshi.desktop" <<EOF
[Desktop Entry]
Name=Tickoshi
Comment=Live Bitcoin price widget
Exec=$BINARY_PATH
Icon=$ICON_PATH
Type=Application
Categories=Utility;Finance;
StartupNotify=false
EOF
    echo "   Desktop launcher created: $DESKTOP_DIR/Tickoshi.desktop"
    echo ""
else
    echo " [ERROR] Build failed. Check output above."
    exit 1
fi
