"""
Tickoshi — BlockClock Mini style desktop widget
Live Bitcoin price, block height, halving countdown.
Frameless, always-on-top, draggable.
"""

import tkinter as tk
import threading
import queue
import json
import os
import sys
import urllib.request
import urllib.error
import time
import html
import re
import datetime
import webbrowser
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import websocket
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageTk

# ── macOS SSL trust store fix ─────────────────────────────────────────────────
# CPython on macOS links its own OpenSSL, which can't read the system Keychain;
# inside a PyInstaller bundle the baked-in cert path doesn't exist either, so
# every HTTPS/WSS handshake fails CERTIFICATE_VERIFY_FAILED. Point OpenSSL at
# certifi's bundle — but only when the default store is actually empty, so a
# healthy setup is never overridden.
if sys.platform == "darwin":
    import ssl
    try:
        if ssl.create_default_context().cert_store_stats()["x509_ca"] == 0:
            import certifi
            os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except Exception:
        pass

# ── Platform font ─────────────────────────────────────────────────────────────
# Bold UI faces to try, in order. Pillow looks bare file names up in the system
# font directories. Unlike Tk, Pillow never substitutes another font for a
# glyph a face lacks, so _load_font walks this list until one covers the text;
# the later entries are there for coverage (currency signs), not looks.
if sys.platform == "darwin":
    _FONT_FILES = ["/System/Library/Fonts/HelveticaNeue.ttc",
                   "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                   "/Library/Fonts/Arial Bold.ttf",
                   "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                   "/Library/Fonts/Arial Unicode.ttf"]
elif os.name == "nt":
    _FONT_FILES = ["segoeuib.ttf", "arialbd.ttf"]
else:
    _FONT_FILES = ["DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "LiberationSans-Bold.ttf"]

# ── Constants ────────────────────────────────────────────────────────────────
APP_NAME   = "Tickoshi"
MAX_DIGITS = 9           # max digit panels (up to 999,999,999)

# Refresh interval presets: label → seconds
REFRESH_OPTIONS = [
    ("1 min",   60),
    ("5 min",   300),
    ("15 min",  900),
    ("30 min",  1800),
    ("1 hr",    3600),
]

# A refresh interval is how often to poll when polling WORKS. A cycle that
# fetched nothing has to come back sooner than that, or a single failure —
# a rate limit, or sockets briefly unavailable while the OS clears a freshly
# launched binary — leaves the price blank for the whole interval, up to an
# hour on the 60-minute setting.
FETCH_RETRY_MIN_S = 5
FETCH_RETRY_MAX_S = 60

DRUM_STEPS = 12
DRUM_MS    = 14

# Size presets offered in the menu, in percent of the base dimensions.
SIZE_PRESETS = (75, 90, 100, 110, 125, 150, 175, 200, 250, 300)
# Any size in between is reachable with Ctrl + mouse wheel, SIZE_STEP at a
# time. The ceiling is about render cost: tiles are drawn at 3x.
SIZE_MIN, SIZE_MAX, SIZE_STEP = 75, 300, 5
# What "100%" means. The BASE_* dimensions below describe a widget that turned
# out much larger than anyone ran it at, so the percentages were recalibrated:
# 100% is now 0.6 of the base dimensions (what used to be called 60%).
# self._scale and the "scale" value in the config are still the factor applied
# to BASE_*, not the percentage, so sizes saved before the change are unchanged
# on screen; only the number shown for them is different.
SIZE_UNIT = 0.6

def _scale_to_pct(scale) -> int:
    return round(scale / SIZE_UNIT * 100)

def _pct_to_scale(pct) -> float:
    return round(pct / 100 * SIZE_UNIT, 4)

def _clamp_scale(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = SIZE_UNIT
    return max(_pct_to_scale(SIZE_MIN), min(_pct_to_scale(SIZE_MAX), round(v, 4)))

# Currencies: code → (Binance symbol, CoinGecko id)
# code → (Binance symbol or None, CoinGecko id). None means Binance has no
# such spot pair, so there is nothing to ask it for: BTC/GBP was delisted
# 2023-12-29 and BTC/RUB on 2024-01-30 with the Russia exit. Requesting either
# returns 400 {"code":-1121,"msg":"Invalid symbol."} every single time.
CURRENCIES = [
    ("USD", "BTCUSDT", "usd"),
    ("TRY", "BTCTRY",  "try"),
    ("EUR", "BTCEUR",  "eur"),
    ("GBP", None,      "gbp"),
    ("JPY", "BTCJPY",  "jpy"),
    ("RUB", None,      "rub"),
]
CURRENCY_TO_GECKO = {code: gid for code, _, gid in CURRENCIES}

# Currency symbols
CURRENCY_SIGNS = {
    "USD": "$", "TRY": "\u20ba", "EUR": "\u20ac",
    "GBP": "\u00a3", "JPY": "\u00a5", "RUB": "\u20bd",
}

# Secondary-row modules (key, menu label). Primary row always shows Price.
MODULES = [
    ("fees",     "Fees"),
    ("sats",     "Sats"),
    ("change",   "24h Change"),
    ("height",   "Block Height"),
    ("blockage", "Block Age"),
    ("halving",  "Halving"),
    ("diff",     "Difficulty"),
    ("hash",     "Hashrate"),
    ("mempool",  "Mempool"),
    ("news",     "News"),
    ("chart",    "Price Chart"),
    ("fng",      "Fear & Greed"),
    ("ath",      "All-Time High"),
]
# Tiles that span the whole row instead of pairing two-per-row.
FULL_ROW_MODULES = ("fees", "news")
def _layout_rows(modules) -> list:
    """The tiles under the price as rows of module keys, top to bottom.

    This is the one definition of the layout: _build_ui builds exactly these
    rows, the window is sized from their number, and reordering moves tiles
    in terms of them. Half-width tiles pair two to a row. A full-row tile is
    placed at once and does NOT end the pair in progress, so a half-width
    tile still waiting for a partner drops below it and pairs with the next
    one. (Ending the run at a full-row tile instead left the odd tile before
    it stretched across a row for good.) So at most one tile is ever alone:
    the last half-width one, when their number is odd. It keeps its own
    place in the order — above any full-row tiles listed after it — which is
    what lets News or Fees be dragged to the very bottom beneath it.
    """
    rows, pending, pending_at = [], [], 0
    for key in modules:
        if key in FULL_ROW_MODULES:
            rows.append([key])
        else:
            if not pending:
                pending_at = len(rows)
            pending.append(key)
            if len(pending) == 2:
                rows.append(pending)
                pending = []
    if pending:
        rows.insert(pending_at, pending)
    return rows

def _count_secondary_rows(modules) -> int:
    return len(_layout_rows(modules))

# How long the mouse must rest on a tile before a drag reorders it instead of
# moving the window, and how far it may wander in that time.
HOLD_MS = 450
HOLD_SLOP_PX = 4

# Tiles fed by the slow loop (_aux_loop) rather than the price loop.
AUX_MODULES = ("news", "chart", "fng", "ath")

# News: free RSS feeds, no key. (name, url, bitcoin_only) — a general crypto
# feed is filtered down to the headlines that mention Bitcoin.
NEWS_FEEDS = [
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/feed", False),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", True),
    ("Cointelegraph", "https://cointelegraph.com/rss/tag/bitcoin", False),
]
NEWS_MAX        = 15      # headlines kept in the ticker
NEWS_TITLE_MAX  = 110     # characters; longer titles are cut with an ellipsis
NEWS_PX_PER_S   = 45      # ticker speed at 100% size
NEWS_TICK_MS    = 30

# The slow loop: headlines, the chart and ATH come round every AUX_REFRESH_S
# whatever the price interval is set to; a round that got nothing retries
# sooner. Fear & Greed only changes once a day, so it has its own floor.
AUX_REFRESH_S = 600
AUX_RETRY_S   = 60
FNG_REFRESH_S = 3600
MODULE_KEYS = {k for k, _ in MODULES}

# Opacity presets
OPACITY_OPTIONS = [("50%", 0.5), ("70%", 0.7), ("85%", 0.85), ("100%", 1.0)]

# Border color themes
BORDER_COLORS = {
    "Gold":   {"hi": "#c9a84c", "lo": "#6a5820"},
    "Orange": {"hi": "#f7931a", "lo": "#9a5a0c"},
    "White":  {"hi": "#cccccc", "lo": "#666666"},
}

# Next Bitcoin halving block
HALVING_INTERVAL = 210_000

# Surface finish of the tiles: Gloss has glass highlights, glows and a
# metallic rim; Matte is the same geometry with all of that turned down.
FINISHES = ("Gloss", "Matte")

# Border pulse animation (price up/down flash)
PULSE_MS         = 30     # ms per animation step
PULSE_STEPS      = 100    # total steps (30*100 = 3000ms fade)
PULSE_LEVELS     = 24     # distinct glow levels; the rim repaints per level

# Panel base dimensions (at scale 1.0)
BASE_PANEL_W = 80
BASE_PANEL_H = 110
BASE_SIGN_W  = 65         # currency sign panel (matches digit visual weight)
BASE_FS      = 62          # digit font size
BASE_R       = 10          # card corner radius

# Layout
FACE_PAD     = 6           # padding inside gold border
PANEL_GAP    = 8           # gap between panels

# Label panel
BASE_LABEL_W  = 78
BASE_LABEL_FS = 16

# Fee block (Price+Fees view) — short, wide; width is set dynamically per build
BASE_FEE_H = 50

# ── Colors ───────────────────────────────────────────────────────────────────
C_FACE       = "#08080a"   # face background; _FACE_RGB is the same colour

# ── Helpers ───────────────────────────────────────────────────────────────────
def _smoothstep(t: float) -> float:
    return t * t * (3 - 2 * t)

def config_path() -> str:
    if os.name == "nt":                        # Windows
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":             # macOS
        base = os.path.join(os.path.expanduser("~"),
                            "Library", "Application Support")
    else:                                      # Linux
        base = os.environ.get("XDG_CONFIG_HOME",
                              os.path.join(os.path.expanduser("~"), ".config"))
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "tickoshi_config.json")

# ── Start at login ───────────────────────────────────────────────────────────
# State lives where the OS keeps it — a registry value, a LaunchAgent plist, an
# autostart .desktop — never mirrored into our own config, so the menu can
# never disagree with what will actually happen at the next login.

def _launch_command() -> list[str]:
    """Argv that starts this app again.

    A PyInstaller onefile build is its own executable; running from source
    needs the interpreter plus the script. `sys.frozen` is what PyInstaller
    sets, and is the only reliable way to tell the two apart.
    """
    if getattr(sys, "frozen", False):
        return [os.path.abspath(sys.executable)]
    return [os.path.abspath(sys.executable), os.path.abspath(__file__)]

def _autostart_path() -> str:
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents",
                            "com.tickoshi.app.plist")
    base = os.environ.get("XDG_CONFIG_HOME",
                          os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "autostart", "tickoshi.desktop")

_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def autostart_enabled() -> bool:
    try:
        if os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_NAME)
            return True
        return os.path.exists(_autostart_path())
    except FileNotFoundError:
        return False
    except OSError:
        return False
    except Exception as e:
        _debug_log(f"autostart check failed: {type(e).__name__}: {e}")
        return False

def set_autostart(enable: bool) -> bool:
    """Register or unregister the app for login. Returns the resulting state,
    so a failure leaves the menu showing what is actually true."""
    cmd = _launch_command()
    try:
        if os.name == "nt":
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as k:
                if enable:
                    # Quote every element: the path routinely contains spaces.
                    winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ,
                                      " ".join(f'"{c}"' for c in cmd))
                else:
                    try:
                        winreg.DeleteValue(k, APP_NAME)
                    except FileNotFoundError:
                        pass
        else:
            path = _autostart_path()
            if not enable:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if sys.platform == "darwin":
                    args = "".join(f"    <string>{c}</string>\n" for c in cmd)
                    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                            '<plist version="1.0"><dict>\n'
                            '  <key>Label</key><string>com.tickoshi.app</string>\n'
                            '  <key>ProgramArguments</key><array>\n'
                            f'{args}'
                            '  </array>\n'
                            '  <key>RunAtLoad</key><true/>\n'
                            '</dict></plist>\n')
                else:
                    quoted = " ".join(
                        c if " " not in c else f'"{c}"' for c in cmd)
                    body = ("[Desktop Entry]\n"
                            "Type=Application\n"
                            f"Name={APP_NAME}\n"
                            f"Exec={quoted}\n"
                            "Terminal=false\n"
                            "X-GNOME-Autostart-enabled=true\n")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body)
    except Exception as e:
        _debug_log(f"autostart {'enable' if enable else 'disable'} failed: "
                   f"{type(e).__name__}: {e}")
    return autostart_enabled()

# ── Render engine ─────────────────────────────────────────────────────────────
# Tk's canvas has no gradients, alpha, blur or antialiasing, so every tile is
# painted with Pillow and shown as one image. Everything is drawn at _SS times
# the final size and scaled down, which is what smooths the rounded corners.
# All of this runs on the Tk thread, from the panels' own draw calls.
_SS = 3
_PT_PX = 4 / 3                 # the BASE_*FS sizes are points; Pillow wants px
_FACE_RGB = (8, 8, 10)         # must equal C_FACE: tiles fade into the face
_WHITE, _SILVER = (255, 255, 255), (196, 200, 210)
_UP_RGB, _DOWN_RGB = (24, 197, 88), (230, 59, 59)

# Colour the OS treats as see-through, painted outside the chassis' rounded
# outline so the window's square corners vanish. Only Windows has a colour
# key (-transparentcolor); elsewhere the corners stay face-coloured. The
# value just has to be one no tile can ever contain.
_CORNER_KEY = "#fe00ff" if os.name == "nt" else None

_render_cache = {}
_font_cache = {}

def _cached(key, build):
    # _build_ui empties this cache on every rebuild, so it only ever holds
    # the tiles of the configuration on screen (about 13 MB at Large). Left
    # to accumulate across sizes, colours and finishes it reached 200 MB.
    hit = _render_cache.get(key)
    if hit is None:
        hit = _render_cache[key] = build()
    return hit

def _rgb(hex_c: str) -> tuple:
    h = hex_c.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def _open_face(name, px):
    f = ImageFont.truetype(name, px)
    if name.lower().endswith(".ttc"):
        # A collection holds every weight; find Bold by name, not by a
        # remembered index that differs between OS releases.
        for i in range(12):
            try:
                g = ImageFont.truetype(name, px, index=i)
            except Exception:
                break
            if g.getname()[1] == "Bold":
                return g
    return f

def _has_glyphs(font, chars):
    """True if `font` can draw every character, not just a missing-glyph box."""
    missing = bytes(font.getmask("\uffff"))
    for ch in chars:
        m = font.getmask(ch)
        if m.getbbox() is None or bytes(m) == missing:
            return False
    return True

def _load_font(px, text=""):
    """Bold UI face at `px` pixels that can draw `text`.

    Takes the first face in _FONT_FILES that loads and covers the non-ASCII
    characters of `text`; if none covers them, the first that loaded.
    """
    px = max(6, int(px))
    need = "".join(sorted({c for c in text if ord(c) > 127}))
    key = (px, need)
    f = _font_cache.get(key)
    if f is not None:
        return f
    first = None
    for name in _FONT_FILES:
        try:
            f = _open_face(name, px)
        except Exception:
            continue
        if first is None:
            first = f
        if not need or _has_glyphs(f, need):
            break
    else:
        # Pillow's built-in scalable face; not pretty, but never missing.
        f = first or ImageFont.load_default(px)
    _font_cache[key] = f
    return f

def _rr_pts(x1, y1, x2, y2, r):
    """Control points for a rounded rectangle as a smoothed Tk polygon."""
    r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
    return [x1+r,y1, x2-r,y1, x2,y1,   x2,y1+r,
            x2,y2-r, x2,y2,   x2-r,y2, x1+r,y2,
            x1,y2,   x1,y2-r, x1,y1+r, x1,y1]

def _fit_font(text, px, max_w):
    """Font at `px`, shrunk if needed so `text` fits in `max_w` pixels.

    Tile proportions were tuned on Segoe UI; DejaVu and the fallback face run
    wider, and a value must never run into its unit.
    """
    f = _load_font(px, text)
    w = f.getlength(text)
    if w > max_w > 0:
        f = _load_font(px * max_w / w, text)
    return f

def _vgrad(w, h, stops):
    """Vertical gradient through (position, rgb) stops."""
    col = Image.new("RGB", (1, 256))
    px = col.load()
    for y in range(256):
        t = y / 255
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t <= t1:
                px[0, y] = _mix(c0, c1, (t - t0) / max(1e-6, t1 - t0))
                break
    return col.resize((max(1, int(w)), max(1, int(h))), Image.BILINEAR)

def _rr_mask(size, box, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=max(0, r), fill=255)
    return m

def _scaled(mask, a):
    return mask.point(lambda v: int(v * a))

def _paint(dst, color, mask):
    dst.paste(color, (0, 0) + dst.size, mask)

def _band(size, y1, y2):
    """Mask that is opaque between two rows — clips an effect to a strip."""
    m = Image.new("L", size, 0)
    m.paste(255, (0, int(y1), size[0], int(y2)))
    return m

def _card_layers(w, h, pad, r, k, hi, lo, finish, seam=False):
    """Raised card as (base, overlay), both at supersampled size.

    Text goes between the two: the overlay carries what sits on top of the
    glyphs — the glass highlight, the edge lighting and the split-flap seam.
    `k` is supersampled pixels per layout unit (scale * _SS).
    """
    key = ("card", w, h, pad, r, round(k, 3), hi, lo, finish, seam)

    def build():
        matte = finish == "Matte"
        size = W, H = w + 2 * pad, h + 2 * pad
        hi_c, lo_c = _rgb(hi), _rgb(lo)
        im = Image.new("RGB", size, _FACE_RGB)
        box = (pad, pad, pad + w, pad + h)
        # The rim, its glow and the drop shadow all have to die out inside
        # `pad`, or the canvas edge cuts them and draws a seam between tiles.
        rw = min(2.0 * k, pad * 0.5)
        ob = (box[0] - rw, box[1] - rw, box[2] + rw, box[3] + rw)
        om = _rr_mask(size, ob, r + rw)
        drop = min(1.4 * k, pad * 0.3)
        sh = _rr_mask(size, (ob[0], ob[1] + drop, ob[2], ob[3] + drop), r + rw)
        _paint(im, (0, 0, 0), sh.filter(ImageFilter.GaussianBlur(pad * 0.22)))
        if not matte:
            glow = om.filter(ImageFilter.GaussianBlur(pad * 0.25))
            _paint(im, hi_c, _scaled(glow, 0.16))
        if matte:
            rim = _vgrad(W, H, [(0, _mix(hi_c, lo_c, 0.25)),
                                (1, _mix(hi_c, lo_c, 0.7))])
        else:
            # Kept close to the accent itself: mixing in white for a
            # "metal" highlight turned Bitcoin orange into peach.
            rim = _vgrad(W, H, [(0, _mix(hi_c, _WHITE, 0.12)), (0.2, hi_c),
                                (0.65, _mix(hi_c, lo_c, 0.7)),
                                (0.88, lo_c),
                                (1, _mix(lo_c, hi_c, 0.6))])
        im.paste(rim, (0, 0), om)
        g = 0.6 * k            # dark hairline between rim and card
        _paint(im, (2, 2, 3), _rr_mask(
            size, (box[0] - g, box[1] - g, box[2] + g, box[3] + g), r + g))

        bm = _rr_mask(size, box, r)
        mid = pad + h // 2
        if seam:
            body = Image.new("RGB", size)
            top = ([(0, (20, 21, 24)), (1, (17, 18, 21))] if matte else
                   [(0, (30, 31, 36)), (0.25, (21, 22, 26)), (1, (14, 15, 18))])
            bot = ([(0, (15, 16, 18)), (1, (12, 13, 15))] if matte else
                   [(0, (11, 12, 14)), (1, (5, 5, 7))])
            body.paste(_vgrad(W, mid, top), (0, 0))
            body.paste(_vgrad(W, H - mid, bot), (0, mid))
        else:
            body = _vgrad(W, H, [(0, (19, 20, 23)), (1, (13, 14, 16))] if matte
                          else [(0, (27, 28, 33)), (0.3, (17, 18, 21)),
                                (1, (7, 7, 9))])
        im.paste(body, (0, 0), bm)

        ov = Image.new("RGBA", size, (0, 0, 0, 0))

        def lay(color, mask):
            layer = Image.new("RGBA", size, color + (0,))
            layer.putalpha(mask)
            ov.alpha_composite(layer)

        if not matte:          # glass highlight fading down the upper part
            gh = int(h * (0.46 if seam else 0.5))
            gm = Image.new("L", size, 0)
            gm.paste(_vgrad(W, gh, [(0, (18,) * 3), (1, (0,) * 3)])
                     .convert("L"), (0, pad))
            lay(_WHITE, ImageChops.multiply(gm, bm))
        # Lit top edge and shaded bottom edge: the card minus itself shifted.
        e = max(1, int(1.2 * k))
        top_e = ImageChops.multiply(
            ImageChops.subtract(bm, ImageChops.offset(bm, 0, e)),
            _band(size, 0, pad + h // 3))
        lay(_WHITE, _scaled(top_e, 0.07 if matte else 0.20))
        e = max(1, int(1.6 * k))
        bot_e = ImageChops.multiply(
            ImageChops.subtract(bm, ImageChops.offset(bm, 0, -e)),
            _band(size, pad + h - h // 3, H))
        lay((0, 0, 0), _scaled(bot_e, 0.3 if matte else 0.6))
        if seam:
            d = ImageDraw.Draw(ov)
            x1, x2 = box[0], box[2]
            d.rectangle((x1, mid - 0.9 * k, x2, mid + 0.9 * k),
                        fill=(2, 2, 3, 255))
            d.rectangle((x1, mid + 0.9 * k, x2, mid + 1.6 * k),
                        fill=(30, 31, 36, 255))
            for nx in (x1, x2 - 3.2 * k):          # hinge notches
                d.rounded_rectangle((nx, mid - 6 * k, nx + 3.2 * k, mid + 6 * k),
                                    radius=1.2 * k, fill=(2, 2, 3, 255))
        return im, ov

    return _cached(key, build)

def _draw_text(dst, x, cy, text, font, top, bot, k, anchor="m",
               matte=False, glow=None):
    """Gradient-filled text with a soft drop shadow.

    `anchor` is horizontal only (l / m / r). Vertically every string is
    centred on the font's cap height, so digits, labels and symbols share one
    optical centre line whatever their own ascenders and descenders do.
    """
    if not text:
        return
    cap = -font.getbbox("0", anchor="ls")[1]
    by = cy + cap / 2
    l, t, r, b = font.getbbox(text, anchor=anchor + "s")
    if r <= l or b <= t:
        return
    # Work on a crop just big enough for the glyphs plus their blur, instead
    # of blurring full-tile masks for a few characters.
    mp = int(13 * k) + 2
    ox, oy = int(x + l) - mp, int(by + t) - mp
    sw, sh = int(r - l) + 2 * mp, int(b - t) + 2 * mp
    m = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(m).text((x - ox, by - oy), text, font=font, fill=255,
                           anchor=anchor + "s")
    bb = m.getbbox()
    if not bb:
        return
    sub = dst.crop((ox, oy, ox + sw, oy + sh))
    shadow = Image.new("L", (sw, sh), 0)
    shadow.paste(m, (0, int(2.2 * k)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(1.6 * k))
    _paint(sub, (0, 0, 0), _scaled(shadow, 0.45 if matte else 0.9))
    if glow and not matte:
        _paint(sub, glow, _scaled(m.filter(ImageFilter.GaussianBlur(5 * k)), 0.35))
    if matte and glow is None:
        top = bot = _mix(top, bot, 0.5)
    fill = Image.new("RGB", (sw, sh), top)
    fill.paste(_vgrad(sw, bb[3] - bb[1], [(0, top), (1, bot)]), (0, bb[1]))
    sub.paste(fill, (0, 0), m)
    dst.paste(sub, (ox, oy))

def _groove(im, box, k):
    """Etched line: a dark cut with a lit lip on its lower/right side."""
    x1, y1, x2, y2 = box
    d = ImageDraw.Draw(im)
    d.rectangle(box, fill=(3, 3, 4))
    if x2 - x1 >= y2 - y1:
        d.rectangle((x1, y2, x2, y2 + 0.8 * k), fill=(34, 35, 40))
    else:
        d.rectangle((x2, y1, x2 + 0.7 * k, y2), fill=(34, 35, 40))

def _finish_tile(im, ov, out_size):
    im.paste(ov, (0, 0), ov)
    return im.resize(out_size, Image.LANCZOS)

def _render_digit(ch, pw, ph, pad, scale, hi, lo, finish, seam=True):
    """One price-row card (digit or currency sign) at final size."""
    key = ("digit", ch, pw, ph, pad, scale, hi, lo, finish, seam)

    def build():
        k = scale * _SS
        w, h, p = pw * _SS, ph * _SS, pad * _SS
        r = max(4, int(BASE_R * scale)) * _SS
        base, ov = _card_layers(w, h, p, r, k, hi, lo, finish, seam=seam)
        im = base.copy()
        fs = max(12, int(BASE_FS * scale)) * _PT_PX * _SS
        _draw_text(im, p + w // 2, p + h // 2, ch, _load_font(fs, ch),
                   _WHITE, _SILVER, k, matte=finish == "Matte")
        return _finish_tile(im, ov, (pw + 2 * pad, ph + 2 * pad))

    return _cached(key, build)

def _render_label(currency, pw, ph, pad, scale, hi, lo, finish):
    """The BTC / <currency> card."""
    k = scale * _SS
    w, h, p = pw * _SS, ph * _SS, pad * _SS
    r = max(4, int(BASE_R * scale)) * _SS
    base, ov = _card_layers(w, h, p, r, k, hi, lo, finish)
    im = base.copy()
    matte = finish == "Matte"
    acc = _rgb(hi)
    font = _load_font(max(8, int(BASE_LABEL_FS * scale)) * _PT_PX * _SS)
    cx, cy = p + w // 2, p + h // 2
    _draw_text(im, cx, cy - int(h * 0.17), "BTC", font, _WHITE, _SILVER, k,
               matte=matte)
    _draw_text(im, cx, cy + int(h * 0.19), currency, font,
               _mix(acc, _WHITE, 0.2), acc, k, matte=matte)
    lw = int(w * 0.6)
    y = cy + int(h * 0.01)
    _groove(im, (cx - lw // 2, y - 0.7 * k, cx + lw // 2, y + 0.6 * k), k)
    ImageDraw.Draw(im).rectangle(
        (cx - lw // 2, y + 0.6 * k, cx + lw // 2, y + 1.4 * k),
        fill=_mix(acc, (0, 0, 0), 0.25))
    return _finish_tile(im, ov, (pw + 2 * pad, ph + 2 * pad))

def _stat_div(pw, scale, div_px=None):
    """Width of a stat tile's label zone, in final pixels."""
    return int(pw * 0.38) if div_px is None else min(div_px, int(pw * 0.38))

def _render_stat(label, value, unit, pw, ph, pad, scale, hi, lo, finish,
                 value_rgb=None, spark=None, div_px=None):
    """A [ LABEL | value  unit ] tile. Signed values are tinted green / red.

    `value_rgb` overrides the value colour, `spark` (a sequence of prices)
    draws a line chart where the value would be, and `div_px` narrows the
    label zone for tiles much wider than half a row.
    """
    k = scale * _SS
    w, h, p = pw * _SS, ph * _SS, pad * _SS
    r = max(4, int(BASE_R * scale * 0.8)) * _SS
    div = _stat_div(pw, scale, div_px) * _SS
    base, ov = _card_layers(w, h, p, r, k, hi, lo, finish)
    im = base.copy()
    matte = finish == "Matte"
    acc = _rgb(hi)
    cy = p + h // 2
    lfs = max(10, int(BASE_LABEL_FS * scale)) * _PT_PX * _SS
    vfs = max(14, int(BASE_FS * scale * 0.40)) * _PT_PX * _SS
    ufs = max(8, int(BASE_LABEL_FS * scale * 0.65)) * _PT_PX * _SS
    _draw_text(im, p + div // 2, cy, label,
               _fit_font(label, lfs, div - 8 * k),
               acc, acc, k, matte=matte)
    x = p + div
    _groove(im, (x - 0.8 * k, p + 1.5 * k, x + 0.4 * k, p + h - 1.5 * k), k)
    top, bot, glow = _WHITE, _SILVER, None
    # "+0.36" / "-6.62" carry a direction; "--" (no data yet) does not.
    if value[:1] in "+-" and value[1:2].isdigit():
        c = _UP_RGB if value[0] == "+" else _DOWN_RGB
        top, bot, glow = _mix(c, _WHITE, 0.45), c, c
    if value_rgb:
        top, bot, glow = _mix(value_rgb, _WHITE, 0.45), value_rgb, value_rgb
    vx = x + max(6, int(8 * scale)) * _SS
    ux = p + w - max(4, int(6 * scale)) * _SS
    ufont = _load_font(ufs, unit)
    room = ux - ufont.getlength(unit) - 5 * k - vx
    # At small sizes the unit sits on its minimum font size and crowds the
    # number. The number is what the tile is for, so the unit gives way
    # first, by up to 30%, before _fit_font has to shrink the value.
    short = _load_font(vfs, value).getlength(value) - room
    if short > 0 and unit:
        uw = ufont.getlength(unit)
        ufont = _fit_font(unit, ufs, max(uw * 0.7, uw - short))
        room = ux - ufont.getlength(unit) - 5 * k - vx
    if spark and len(spark) >= 2:
        _draw_spark(im, (vx, p + h * 0.22, vx + room - 5 * k, p + h * 0.78), spark, k,
                    matte)
    else:
        _draw_text(im, vx, cy, value, _fit_font(value, vfs, room),
                   top, bot, k, anchor="l", matte=matte, glow=glow)
    _draw_text(im, ux, cy, unit, ufont, (150, 153, 163), (112, 115, 124), k,
               anchor="r", matte=matte)
    return _finish_tile(im, ov, (pw + 2 * pad, ph + 2 * pad))

def _draw_spark(im, box, points, k, matte=False):
    """Line chart of `points` inside `box`: green if it ends up, red if down."""
    x1, y1, x2, y2 = box
    if x2 - x1 < 4 * k:
        return
    lo_v, hi_v = min(points), max(points)
    span = (hi_v - lo_v) or 1.0
    n = len(points) - 1
    xy = [(x1 + (x2 - x1) * i / n, y2 - (y2 - y1) * (v - lo_v) / span)
          for i, v in enumerate(points)]
    c = _UP_RGB if points[-1] >= points[0] else _DOWN_RGB
    size = im.size
    # Soft fill under the line, fading towards the bottom.
    area = Image.new("L", size, 0)
    ImageDraw.Draw(area).polygon(xy + [(x2, y2), (x1, y2)], fill=255)
    fade = Image.new("L", size, 0)
    fade.paste(_vgrad(size[0], y2 - y1, [(0, (70,) * 3), (1, (0,) * 3)])
               .convert("L"), (0, int(y1)))
    _paint(im, c, ImageChops.multiply(area, fade))
    line = Image.new("L", size, 0)
    ImageDraw.Draw(line).line(xy, fill=255, width=max(2, int(1.7 * k)),
                              joint="curve")
    if not matte:
        _paint(im, c, _scaled(line.filter(ImageFilter.GaussianBlur(2.5 * k)), 0.5))
    _paint(im, _mix(c, _WHITE, 0.25), line)
    d = ImageDraw.Draw(im)
    ex, ey = xy[-1]
    d.ellipse((ex - 2.4 * k, ey - 2.4 * k, ex + 2.4 * k, ey + 2.4 * k),
              fill=_mix(c, _WHITE, 0.5))

def _render_news_strip(items, pw, ph, pad, scale, hi, lo, finish):
    """Every headline side by side as one long image, plus where each sits.

    Returns (image, [(x_start, x_end, url)]). The strip is as tall as the
    tile and carries the card's own background, so the ticker only has to
    slide it: nothing is re-rendered while it scrolls. Each headline is
    drawn and scaled down on its own — one supersampled image of the whole
    strip would run to tens of megapixels.
    """
    k = scale * _SS
    w, h, p = pw * _SS, ph * _SS, pad * _SS
    r = max(4, int(BASE_R * scale * 0.8)) * _SS
    base, ov = _card_layers(w, h, p, r, k, hi, lo, finish)
    H = h + 2 * p
    # The card is uniform across its middle, so one column of it is the
    # background (and the glass on top) for any width of strip.
    col_x = p + w // 2
    bg_col = base.crop((col_x, 0, col_x + 1, H))
    ov_col = ov.crop((col_x, 0, col_x + 1, H))
    matte = finish == "Matte"
    acc = _rgb(hi)
    fs = max(10, int(BASE_LABEL_FS * scale)) * _PT_PX * _SS
    gap = int(30 * k)
    cy = p + h // 2
    parts, spans, x = [], [], 0
    for title, url in items:
        font = _load_font(fs, title)
        seg_w = (int(font.getlength(title)) + gap) // _SS * _SS + _SS
        seg = bg_col.resize((seg_w, H))
        d = ImageDraw.Draw(seg)
        d.ellipse((gap / 2 - 2.6 * k, cy - 2.6 * k, gap / 2 + 2.6 * k,
                   cy + 2.6 * k), fill=acc)
        _draw_text(seg, gap, cy, title, font, _WHITE, _SILVER, k, anchor="l",
                   matte=matte)
        glass = ov_col.resize((seg_w, H))
        seg.paste(glass, (0, 0), glass)
        seg = seg.resize((seg_w // _SS, H // _SS), Image.LANCZOS)
        parts.append(seg)
        spans.append((x, x + seg.size[0], url))
        x += seg.size[0]
    strip = Image.new("RGB", (max(1, x), H // _SS), _FACE_RGB)
    for seg, (x0, _, _) in zip(parts, spans):
        strip.paste(seg, (x0, 0))
    return strip, spans

def _flip_frame(old, new, t, top):
    """One frame of the split-flap fold between two finished card images.

    First half: the old top flap folds down over the new top. Second half:
    the new bottom flap swings down over the old bottom. `top` is where the
    card starts inside the tile, so the flap hinges on the seam.
    """
    W, H = old.size
    mid = H // 2
    out = old.copy()
    out.paste(new.crop((0, 0, W, mid)), (0, 0))
    if t < 0.5:
        f = 1 - t * 2
        fh = max(1, int((mid - top) * f))
        flap = old.crop((0, top, W, mid)).resize((W, fh), Image.BILINEAR)
        flap = Image.blend(flap, Image.new("RGB", flap.size), 0.55 * (1 - f))
        out.paste(flap, (0, mid - fh))
    else:
        f = (t - 0.5) * 2
        fh = max(1, int((H - top - mid) * f))
        flap = new.crop((0, mid, W, H - top)).resize((W, fh), Image.BILINEAR)
        flap = Image.blend(flap, Image.new("RGB", flap.size), 0.45 * (1 - f))
        out.paste(flap, (0, mid))
    return out

def _frame_layers(w, h, scale):
    """Masks for the chassis rim at final size: ring, inner line, two glows,
    and the area outside the rounded outline."""
    def build():
        W, H = w * _SS, h * _SS
        k = scale * _SS
        R, bw = 9 * k, 3.0 * k
        outer = _rr_mask((W, H), (0, 0, W - 1, H - 1), R)
        inner = _rr_mask((W, H), (bw, bw, W - 1 - bw, H - 1 - bw), R - bw)
        inner2 = _rr_mask((W, H), (bw + k, bw + k, W - 1 - bw - k,
                                   H - 1 - bw - k), R - bw - k)
        down = lambda m: m.resize((w, h), Image.LANCZOS)
        ring = down(ImageChops.subtract(outer, inner))
        line = down(ImageChops.subtract(inner, inner2))
        # Blur at final size: the window can be 1400px wide, and blurring it
        # supersampled costs far more than the result is worth.
        tight = ring.filter(ImageFilter.GaussianBlur(5 * scale))
        wide = ring.filter(ImageFilter.GaussianBlur(9 * scale))
        # Hard-edged: everything outside the rounded outline, for the
        # colour-keyed (see-through) window corners.
        outside = down(outer).point(lambda v: 255 if v < 128 else 0)
        return ring, line, tight, wide, outside
    return _cached(("frame", w, h, scale), build)

def _render_frame(layers, hi, lo, finish, pulse=None, amount=0.0):
    """Chassis rim. `pulse` (rgb) at `amount` 0..1 floods it with the flash."""
    ring, line, tight, wide, outside = layers
    matte = finish == "Matte"
    c, cl = _rgb(hi), _rgb(lo)
    if pulse and amount > 0:
        c = _mix(c, pulse, amount)
        cl = _mix(cl, _mix(pulse, (0, 0, 0), 0.5), amount)
    else:
        amount = 0.0
    im = Image.new("RGB", ring.size, _FACE_RGB)
    rest = 0.0 if matte else 0.55
    if rest:
        _paint(im, c, _scaled(tight, rest * (1 - amount)))
    if amount:
        _paint(im, pulse, _scaled(wide, amount))
    W, H = ring.size
    if matte:
        rim = _vgrad(W, H, [(0, _mix(c, cl, 0.2)), (1, _mix(c, cl, 0.65))])
    else:
        rim = _vgrad(W, H, [(0, _mix(c, _WHITE, 0.15)), (0.06, c),
                            (0.5, _mix(c, cl, 0.6)), (0.94, c),
                            (1, _mix(c, cl, 0.5))])
    im.paste(rim, (0, 0), ring)
    _paint(im, (0, 0, 0), _scaled(line, 0.85))
    if _CORNER_KEY:
        _paint(im, _rgb(_CORNER_KEY), outside)
    return im

# ── Price API ─────────────────────────────────────────────────────────────────
_price_cache = {}
_block_height_cache = {"height": None}
_fees_cache = {"low": None, "med": None, "high": None}
_network_cache = {"hashrate_ehs": None, "mempool_mb": None,
                  # Difficulty retarget, from the WS "da" object.
                  "diff_change_pct": None, "diff_blocks_left": None,
                  # Newest block seen, for the block-age counter.
                  "block_ts": None,
                  # Bumped once per block mined while we are connected; the
                  # UI flashes the rim when it sees the number move.
                  "block_seq": 0}
# Headlines for the ticker as (title, url); seq moves when the list changes.
_news_cache = {"items": [], "seq": 0}
# Slow-loop data. ath_pct and spark are keyed by CoinGecko id like the price.
_extras_cache = {"ath_pct": {}, "spark": {}, "fng": None, "fng_label": "",
                 "fng_at": 0.0}
_aux_logged = set()
# 24h price move per currency, keyed by CoinGecko id like _price_cache.
_change_cache = {}
# Dedup for HTTP value lines, same idea as _ws_logged: a 200-line log must not
# be filled by values that repeat every cycle.
_price_logged = {}
_price_cache_lock = threading.Lock()

def _fetch_all_prices(preferred: str | None = None):
    """Refresh the price cache. Returns True if *this call* obtained a live
    quote for `preferred`; the cache keeps the last good price, so asking
    whether it is non-empty would report success long after the network went
    away."""
    gecko_ids = ",".join(gid for _, _, gid in CURRENCIES)
    pref_gid = CURRENCY_TO_GECKO.get(preferred)
    stored = set()
    try:
        # include_24hr_change rides along on the existing request — the 24h
        # move costs no extra call, and Binance has no equivalent here, so the
        # tile falls back to "--" whenever the fallback supplied the price.
        url = ("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin"
               "&include_24hr_change=true&vs_currencies=" + gecko_ids)
        req = urllib.request.Request(url, headers={"User-Agent": "Tickoshi/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        quotes = data.get("bitcoin") or {}
        with _price_cache_lock:
            for _, _, gid in CURRENCIES:
                v = quotes.get(gid)
                if isinstance(v, (int, float)) and v > 0:
                    _price_cache[gid] = float(v)
                    stored.add(gid)
                chg = quotes.get(f"{gid}_24h_change")
                if isinstance(chg, (int, float)):
                    _change_cache[gid] = float(chg)
        if quotes and not _price_logged.get("fields"):
            # One-shot: says whether include_24hr_change is actually being
            # honoured, which is otherwise indistinguishable from a flat market.
            _price_logged["fields"] = True
            _debug_log(f"http price (coingecko) fields: {sorted(quotes)}")
        if pref_gid:
            chg_now = _change_cache.get(pref_gid)
            if _price_logged.get("chg") != chg_now:
                _price_logged["chg"] = chg_now
                _debug_log(f"http price  24h change {pref_gid}={chg_now!r}")
        if not stored:
            # CoinGecko answers 200 with an empty or error-shaped body when it
            # is rate limiting. Counting the request itself as success skipped
            # the fallback below and left the price blank until the next
            # restart, so a usable quote is what counts as success.
            _debug_log(f"http price (coingecko) returned no usable quotes: "
                       f"{str(quotes)[:120]}")
    except Exception as e:
        _debug_log(f"http price fetch failed (coingecko): {type(e).__name__}: {e}")

    def got_wanted():
        # A response carrying every currency but the one on screen still
        # leaves the display blank, so success keys off the wanted quote.
        return (pref_gid in stored) if pref_gid else bool(stored)

    if not got_wanted():
        # Only the selected currency is ever displayed and switching currency
        # kicks off a fresh fetch, so stop at the first symbol that works
        # rather than walking them all at 5s a piece while the network is down.
        order = [c for c in CURRENCIES if c[0] == preferred]
        order += [c for c in CURRENCIES if c not in order]
        for code, bsym, gid in order:
            if bsym is None:            # no such pair — nothing to ask for
                continue
            try:
                url = "https://api.binance.com/api/v3/ticker/price?symbol=" + bsym
                req = urllib.request.Request(url, headers={"User-Agent": "Tickoshi/1.0"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    data = json.loads(r.read().decode())
                price = float(data["price"])
            except Exception as e:
                _debug_log(f"http price fetch failed (binance {bsym}): {type(e).__name__}: {e}")
                continue
            if price > 0:
                with _price_cache_lock:
                    _price_cache[gid] = price
                stored.add(gid)
                if preferred is None or code == preferred:
                    break

    return got_wanted()

def _fetch_hashrate():
    """Populate _network_cache['hashrate_ehs'] from mempool.space's free
    mining endpoint. Endpoint returns {"currentHashrate": <H/s>, ...}."""
    try:
        url = "https://mempool.space/api/v1/mining/hashrate/3d"
        req = urllib.request.Request(url, headers={"User-Agent": "Tickoshi/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        hr = data.get("currentHashrate")
        if isinstance(hr, (int, float)) and hr > 0:
            with _price_cache_lock:
                _network_cache["hashrate_ehs"] = hr / 1e18
            _debug_log(f"http hashrate  H/s={hr} -> {_network_cache['hashrate_ehs']:.2f} EH/s")
            return _network_cache["hashrate_ehs"]
    except Exception as e:
        _debug_log(f"http hashrate fetch failed: {type(e).__name__}: {e}")
    return None

def _fetch_block_height():
    try:
        url = "https://blockchain.info/q/getblockcount"
        req = urllib.request.Request(url, headers={"User-Agent": "Tickoshi/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            height = int(r.read().decode().strip())
        with _price_cache_lock:
            _block_height_cache["height"] = height
        return height
    except Exception:
        return _block_height_cache.get("height")

_UA_BROWSERISH = "Mozilla/5.0 (compatible; Tickoshi/1.4)"

def _log_once(key, msg):
    """Slow-loop failures repeat every round; say each distinct one once."""
    if key not in _aux_logged:
        _aux_logged.add(key)
        _debug_log(msg)

def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

def _feed_time(text):
    """Unix time from an RSS (RFC 822) or Atom (ISO 8601) date, else 0."""
    text = (text or "").strip()
    if not text:
        return 0.0
    try:
        return parsedate_to_datetime(text).timestamp()
    except Exception:
        pass
    try:
        dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0

def _clean_title(text):
    text = html.unescape(re.sub(r"<[^>]+>", "", text or ""))
    text = " ".join(text.split())
    if len(text) > NEWS_TITLE_MAX:
        text = text[:NEWS_TITLE_MAX - 1].rstrip() + "…"
    return text

def _parse_feed(raw, bitcoin_only=False):
    """[(unix_time, title, url)] from RSS 2.0 or Atom bytes.

    Everything in a feed is untrusted. The link ends up in webbrowser.open,
    so anything that is not plain http(s) is dropped here, at the door.
    """
    out = []
    root = ET.fromstring(raw)
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title, link, when = "", "", ""
        for ch in el:
            name = _local(ch.tag)
            if name == "title":
                title = "".join(ch.itertext())
            elif name == "link":
                href = ch.get("href")
                if href:                              # Atom
                    if ch.get("rel", "alternate") == "alternate" or not link:
                        link = href
                elif ch.text and not link:            # RSS
                    link = ch.text
            elif name in ("pubDate", "published", "updated", "date") and not when:
                when = ch.text or ""
        title = _clean_title(title)
        link = (link or "").strip()
        if not title or not link.lower().startswith(("http://", "https://")):
            continue
        if bitcoin_only and not re.search(r"\b(bitcoin|btc)\b", title, re.I):
            continue
        out.append((_feed_time(when), title, link))
    return out

def _fetch_news():
    """Refresh _news_cache from NEWS_FEEDS. True if any feed answered.

    One feed being down must not empty the ticker, so each is fetched on its
    own and whatever arrived is merged, newest first.
    """
    found = []
    for name, url, bitcoin_only in NEWS_FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA_BROWSERISH})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read(2_000_000)
            found.extend(_parse_feed(raw, bitcoin_only))
        except Exception as e:
            _log_once(("news", name, type(e).__name__),
                      f"news feed failed ({name}): {type(e).__name__}: {e}")
    if not found:
        return False
    found.sort(key=lambda it: it[0], reverse=True)
    items, seen = [], set()
    for _, title, link in found:
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append((title, link))
        if len(items) >= NEWS_MAX:
            break
    with _price_cache_lock:
        if items != _news_cache["items"]:
            _news_cache["items"] = items
            _news_cache["seq"] += 1
    return True

def _fetch_market_extras(currency):
    """ATH distance and the last 24 hourly prices, in one CoinGecko request."""
    gid = CURRENCY_TO_GECKO.get(currency, "usd")
    try:
        url = ("https://api.coingecko.com/api/v3/coins/markets?ids=bitcoin"
               "&sparkline=true&vs_currency=" + gid)
        req = urllib.request.Request(url, headers={"User-Agent": "Tickoshi/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        row = data[0] if isinstance(data, list) and data else {}
        ath = row.get("ath_change_percentage")
        pts = (row.get("sparkline_in_7d") or {}).get("price") or []
        pts = [float(p) for p in pts[-24:] if isinstance(p, (int, float))]
        with _price_cache_lock:
            if isinstance(ath, (int, float)):
                _extras_cache["ath_pct"][gid] = float(ath)
            if len(pts) >= 2:
                _extras_cache["spark"][gid] = tuple(pts)
        if isinstance(ath, (int, float)) or len(pts) >= 2:
            return True
        _log_once(("extras", "empty"),
                  f"http market extras returned nothing usable: {str(data)[:120]}")
    except Exception as e:
        _log_once(("extras", type(e).__name__),
                  f"http market extras failed: {type(e).__name__}: {e}")
    return False

def _fetch_fear_greed():
    """Fear & Greed index (0-100) from alternative.me. Published daily."""
    with _price_cache_lock:
        fresh = (_extras_cache["fng"] is not None
                 and time.time() - _extras_cache["fng_at"] < FNG_REFRESH_S)
    if fresh:
        return True
    try:
        req = urllib.request.Request("https://api.alternative.me/fng/?limit=1",
                                     headers={"User-Agent": "Tickoshi/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        row = (data.get("data") or [{}])[0]
        value = int(row["value"])
        if 0 <= value <= 100:
            with _price_cache_lock:
                _extras_cache["fng"] = value
                _extras_cache["fng_label"] = str(row.get("value_classification") or "")[:14]
                _extras_cache["fng_at"] = time.time()
            return True
    except Exception as e:
        _log_once(("fng", type(e).__name__),
                  f"http fear&greed failed: {type(e).__name__}: {e}")
    return False

def _fng_rgb(value):
    """Colour for a Fear & Greed reading; None means the normal white."""
    if value < 25:
        return _DOWN_RGB
    if value < 45:
        return (247, 147, 26)
    if value < 55:
        return None
    if value < 75:
        return (150, 220, 120)
    return _UP_RGB

def _fmt_signed_pct(v):
    """Signed percentage for the 24h and difficulty tiles.

    Two decimals below 10% because a daily move of 0.4% and one of 0.44% are
    different numbers, one above because the tile is narrow and nobody needs
    hundredths of a 30% swing. Rounds away a signed zero: -0.04 formatted at
    one decimal reads "-0.0", which looks like a bug.
    """
    if not isinstance(v, (int, float)):
        return "--"
    out = f"{v:+.2f}" if abs(v) < 10 else f"{v:+.1f}"
    if float(out) == 0:
        return "0.00" if abs(v) < 10 else "0.0"
    return out

def _fmt_fee(v):
    """Format a sat/vB number: fractional below 10, integer above.
    Also strips trailing .0 (e.g. 2.0 -> 2)."""
    if not isinstance(v, (int, float)):
        return None
    if v >= 10:
        return int(round(v))
    rounded = round(v, 1)
    if rounded == int(rounded):
        return int(rounded)
    return rounded

_DEBUG_LOG_MAX_LINES = 200
_debug_log_lock = threading.Lock()

def _debug_log(msg: str):
    # Called from the WS thread, HTTP fetch threads, and the Tk main thread.
    # The read-append-trim-write cycle is not atomic, so guard it with a lock
    # or concurrent writers can truncate each other's lines.
    try:
        path = os.path.join(os.path.dirname(config_path()), "tickoshi_debug.log")
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts}  {msg}\n"
        with _debug_log_lock:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    old = f.readlines()
            except FileNotFoundError:
                old = []
            old.append(line)
            if len(old) > _DEBUG_LOG_MAX_LINES:
                old = old[-_DEBUG_LOG_MAX_LINES:]
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(old)
    except Exception:
        pass

# ── Mempool.space WebSocket: source of truth for Priority tile values ─────────
# Public, free endpoint. Pushes live `fees` object with fractional sat/vB values
# that /fees/recommended REST clamps to integer >= 1.
_ws_thread = None
_ws_stop_flag = threading.Event()
_ws_app = None  # active WebSocketApp instance (for .close())
_ws_keys_logged = False  # one-shot top-level-key dump per connection
_ws_lock = threading.Lock()
_ws_gen = 0          # identity of the run allowed to own the socket
_ws_running = False  # a run at the current generation is serving
_ws_logged = {}      # last logged value per feed, to keep the log from churning

WS_URL           = "wss://mempool.space/api/v1/ws"
WS_BACKOFF_MIN_S = 5
WS_BACKOFF_MAX_S = 60
WS_STABLE_S      = 60   # a connection up this long earns a fresh backoff

def _ws_on_open(ws):
    global _ws_keys_logged
    _ws_keys_logged = False
    _ws_logged.clear()
    _debug_log("ws CONNECTED")
    try:
        ws.send(json.dumps({"action": "init"}))
        ws.send(json.dumps({"action": "want",
                            "data": ["stats", "mempool-blocks"]}))
    except Exception as e:
        _debug_log(f"ws subscribe fail: {type(e).__name__}: {e}")

def _ws_on_message(ws, raw):
    global _ws_keys_logged
    try:
        msg = json.loads(raw)
    except Exception:
        return
    if not isinstance(msg, dict):
        return

    if not _ws_keys_logged:
        _debug_log(f"ws first-msg keys: {sorted(msg.keys())}")
        _ws_keys_logged = True

    # Fees — pushed as {"fees": {fastestFee, halfHourFee, hourFee, ...}}
    fees = msg.get("fees")
    if isinstance(fees, dict):
        high = fees.get("fastestFee")
        med  = fees.get("halfHourFee")
        low  = fees.get("hourFee")
        with _price_cache_lock:
            _fees_cache["high"] = _fmt_fee(high)
            _fees_cache["med"]  = _fmt_fee(med)
            _fees_cache["low"]  = _fmt_fee(low)
        shown = (_fees_cache["high"], _fees_cache["med"], _fees_cache["low"])
        # mempool.space re-pushes fees every few seconds. Logging every push
        # filled the whole 200-line window in about two minutes and scrolled
        # every connection error out of it, so only a change earns a line.
        if _ws_logged.get("fees") != shown:
            _ws_logged["fees"] = shown
            _debug_log(
                f"ws fees  raw=(fastest={high!r}, halfHour={med!r}, hour={low!r}, "
                f"economy={fees.get('economyFee')!r}, minimum={fees.get('minimumFee')!r})  "
                f"formatted=(HIGH={shown[0]!r}, MED={shown[1]!r}, LOW={shown[2]!r})"
            )

    # Mempool size — {"mempoolInfo": {"bytes": <vBytes>, "size": <tx_count>, ...}}
    mi = msg.get("mempoolInfo")
    if isinstance(mi, dict):
        vbytes = mi.get("bytes") or mi.get("vsize")
        if isinstance(vbytes, (int, float)) and vbytes >= 0:
            with _price_cache_lock:
                _network_cache["mempool_mb"] = vbytes / 1_000_000.0
            mb = _network_cache["mempool_mb"]
            if _ws_logged.get("mempool") != round(mb, 2):
                _ws_logged["mempool"] = round(mb, 2)
                _debug_log(f"ws mempool  bytes={vbytes} -> {mb:.2f} MB")

    # Difficulty adjustment — {"da": {"currentHashrate", "difficultyChange",
    # "remainingBlocks", ...}}. The hashrate tile already parsed this object;
    # the retarget fields were arriving and being thrown away.
    da = msg.get("da")
    if isinstance(da, dict):
        hr = da.get("currentHashrate")
        if isinstance(hr, (int, float)) and hr > 0:
            with _price_cache_lock:
                _network_cache["hashrate_ehs"] = hr / 1e18
            ehs = _network_cache["hashrate_ehs"]
            if _ws_logged.get("hashrate") != round(ehs, 2):
                _ws_logged["hashrate"] = round(ehs, 2)
                _debug_log(f"ws hashrate  H/s={hr} -> {ehs:.2f} EH/s")
        chg  = da.get("difficultyChange")
        left = da.get("remainingBlocks")
        with _price_cache_lock:
            if isinstance(chg, (int, float)):
                _network_cache["diff_change_pct"] = float(chg)
            if isinstance(left, (int, float)) and left >= 0:
                _network_cache["diff_blocks_left"] = int(left)

    # Blocks — the socket sends an array on connect and a single object for
    # each new block. Both carry height and a unix timestamp, which gives the
    # block-age counter and a height that lands the moment a block is mined
    # rather than on the next HTTP poll.
    found = []
    blks = msg.get("blocks")
    if isinstance(blks, list):
        found.extend(b for b in blks if isinstance(b, dict))
    blk = msg.get("block")
    if isinstance(blk, dict):
        found.append(blk)
    newest = None
    for b in found:
        h = b.get("height")
        if isinstance(h, int) and (newest is None or h > newest.get("height", -1)):
            newest = b
    if newest is not None:
        h  = newest.get("height")
        ts = newest.get("timestamp")
        with _price_cache_lock:
            if isinstance(ts, (int, float)) and ts > 0:
                _network_cache["block_ts"] = float(ts)
            if isinstance(h, int) and h > (_block_height_cache.get("height") or 0):
                # Only a lone "block" push is a block mined just now; the
                # "blocks" array on connect is history and must not flash.
                if newest is blk and _block_height_cache.get("height"):
                    _network_cache["block_seq"] += 1
                _block_height_cache["height"] = h
                _debug_log(f"ws block  height={h}")

    # Hashrate fallback — init payload carries a "hashrates" array of historical points.
    hrs = msg.get("hashrates")
    if isinstance(hrs, list) and hrs:
        last = hrs[-1]
        if isinstance(last, dict):
            hr = last.get("avgHashrate")
            if isinstance(hr, (int, float)) and hr > 0:
                with _price_cache_lock:
                    # Don't overwrite a fresher "da.currentHashrate" value.
                    if _network_cache["hashrate_ehs"] is None:
                        _network_cache["hashrate_ehs"] = hr / 1e18

def _ws_on_error(ws, err):
    _debug_log(f"ws ERROR: {type(err).__name__}: {err}")

def _ws_on_close(ws, code, reason):
    _debug_log(f"ws CLOSED code={code!r} reason={reason!r}")

def _ws_run(gen):
    """Connect-and-reconnect loop for one generation of the feed.

    `gen` is this run's claim on the socket: _ws_start/_ws_stop bump the global
    generation, so a run left over from an earlier stop retires at its next
    check instead of lingering or fighting over _ws_app.
    """
    global _ws_app, _ws_running
    backoff = WS_BACKOFF_MIN_S
    while True:
        with _ws_lock:
            if gen != _ws_gen or _ws_stop_flag.is_set():
                break
        started = time.monotonic()
        try:
            app = websocket.WebSocketApp(
                WS_URL,
                on_open    = _ws_on_open,
                on_message = _ws_on_message,
                on_error   = _ws_on_error,
                on_close   = _ws_on_close,
            )
            with _ws_lock:
                if gen != _ws_gen:
                    break
                _ws_app = app
            app.run_forever(ping_interval=25, ping_timeout=10)
        except Exception as e:
            _debug_log(f"ws run_forever crash: {type(e).__name__}: {e}")
        up = time.monotonic() - started
        with _ws_lock:
            if gen != _ws_gen or _ws_stop_flag.is_set():
                break
        # A connection that stayed up is evidence the network is healthy, so
        # the next drop retries promptly. Without this the backoff only ever
        # grew, and a few suspend/resume cycles left every later reconnect
        # waiting the full minute for the rest of the session.
        if up >= WS_STABLE_S:
            backoff = WS_BACKOFF_MIN_S
        _debug_log(f"ws disconnected after {up:.0f}s; retry in {backoff}s")
        _ws_stop_flag.wait(backoff)
        backoff = min(backoff * 2, WS_BACKOFF_MAX_S)

    with _ws_lock:
        if gen == _ws_gen:
            _ws_running = False
            _ws_app = None

def _ws_start():
    global _ws_thread, _ws_gen, _ws_running
    with _ws_lock:
        _ws_stop_flag.clear()
        if _ws_running and _ws_thread is not None and _ws_thread.is_alive():
            return
        # Retire any run still winding down from a previous _ws_stop(). The old
        # is_alive() check alone let a stop-then-start — toggling a WS tile off
        # and straight back on — return early against a thread that was about
        # to exit, leaving the feed dead until the app restarted.
        _ws_gen += 1
        gen = _ws_gen
        _ws_running = True
        _ws_thread = threading.Thread(target=_ws_run, args=(gen,), daemon=True)
        _ws_thread.start()

def _ws_stop():
    global _ws_gen, _ws_running, _ws_app
    with _ws_lock:
        _ws_gen += 1
        _ws_running = False
        _ws_stop_flag.set()
        app, _ws_app = _ws_app, None
    if app is not None:
        try:
            app.close()
        except Exception:
            pass

def fetch_price(currency: str = "USD") -> str | None:
    gecko_id = CURRENCY_TO_GECKO.get(currency, "usd")
    with _price_cache_lock:
        price = _price_cache.get(gecko_id)
    if price is not None:
        return str(int(round(price)))
    return None

def next_halving_block(height: int) -> int:
    """The next halving at or after `height`. Derived rather than hardcoded: a
    fixed constant silently turns the tile into "--" forever once that block is
    mined, in every binary already shipped."""
    return (height // HALVING_INTERVAL + 1) * HALVING_INTERVAL

def calc_halving_days(height: int) -> int | None:
    if not isinstance(height, int) or height < 0:
        return None
    blocks_remaining = next_halving_block(height) - height
    minutes_remaining = blocks_remaining * 10
    return int(minutes_remaining / 60 / 24)

# ── Panels ────────────────────────────────────────────────────────────────────
# Each panel is a canvas holding a single Pillow-rendered image. The size
# maths is unchanged from the flat design, so the layout code in _build_ui
# does not need to know how a tile is painted.
class _ImagePanel(tk.Canvas):

    def __init__(self, parent, **kw):
        super().__init__(parent, width=self._pw + self._pad * 2,
                         height=self._ph + self._pad * 2,
                         bg=C_FACE, highlightthickness=0, **kw)
        self._photo = None
        self._img_id = self.create_image(0, 0, anchor="nw")

    def _show(self, img):
        # Keep the PhotoImage on self: Tk only holds a weak name for it, and
        # a collected image silently renders as nothing.
        self._photo = ImageTk.PhotoImage(img)
        self.itemconfig(self._img_id, image=self._photo)

    def _style(self):
        return self._border_hi, self._border_lo, self._finish

    def set_mark(self, color=None):
        """Outline the card in `color` (None removes it). Used while tiles
        are being dragged into a new order; a canvas item, not a re-render."""
        self.delete("mark")
        if color:
            p, w = self._pad, max(2, int(2 * self._scale))
            pts = _rr_pts(p, p, p + self._pw, p + self._ph,
                          max(4, int(BASE_R * self._scale * 0.8)))
            self.create_polygon(pts, smooth=True, fill="", outline=color,
                                width=w, tags="mark")


# ── Digit Panel (split-flap card) ────────────────────────────────────────────
class DigitPanel(_ImagePanel):

    def __init__(self, parent, scale=1.0, border_hi="#c9a84c",
                 border_lo="#6a5820", finish="Gloss", **kw):
        self._scale = scale
        self._border_hi, self._border_lo, self._finish = border_hi, border_lo, finish
        self._pw  = max(30, int(BASE_PANEL_W * scale))
        self._ph  = max(40, int(BASE_PANEL_H * scale))
        self._pad = max(3, int(4 * scale))
        self._cur  = ""
        self._nxt  = ""
        self._busy = False
        super().__init__(parent, **kw)
        self._show(self._face(self._cur))

    def _face(self, ch):
        return _render_digit(ch, self._pw, self._ph, self._pad, self._scale,
                             *self._style())

    def set(self, digit: str):
        self._nxt = digit
        if self._busy or digit == self._cur:
            return
        self._busy = True
        self._flip(0, self._cur, digit)

    def _flip(self, step, old, new):
        if not self.winfo_exists():
            return
        if step >= DRUM_STEPS:
            self._cur = new
            self._show(self._face(new))
            if self._nxt != new:
                # The target moved while this flip was in the air.
                self._flip(0, new, self._nxt)
            else:
                self._busy = False
            return
        t = _smoothstep(step / DRUM_STEPS)
        top = max(0, self._pad - int(2 * self._scale) - 1)
        self._show(_flip_frame(self._face(old), self._face(new), t, top))
        self.after(DRUM_MS, lambda: self._flip(step + 1, old, new))


# ── Sign Panel (currency symbol) ─────────────────────────────────────────────
class SignPanel(_ImagePanel):

    def __init__(self, parent, scale=1.0, symbol="$", border_hi="#c9a84c",
                 border_lo="#6a5820", finish="Gloss", **kw):
        self._scale = scale
        self._symbol = symbol
        self._border_hi, self._border_lo, self._finish = border_hi, border_lo, finish
        self._pw  = max(20, int(BASE_SIGN_W * scale))
        self._ph  = max(40, int(BASE_PANEL_H * scale))
        self._pad = max(3, int(4 * scale))
        super().__init__(parent, **kw)
        self._show(_render_digit(self._symbol, self._pw, self._ph, self._pad,
                                 self._scale, *self._style(), seam=False))


# ── Label Panel (BTC / currency) ─────────────────────────────────────────────
class LabelPanel(_ImagePanel):

    def __init__(self, parent, scale=1.0, currency="USD", border_hi="#c9a84c",
                 border_lo="#6a5820", finish="Gloss", **kw):
        self._scale = scale
        self._currency = currency
        self._border_hi, self._border_lo, self._finish = border_hi, border_lo, finish
        self._pw  = max(30, int(BASE_LABEL_W * scale))
        self._ph  = max(40, int(BASE_PANEL_H * scale))
        self._pad = max(3, int(4 * scale))
        super().__init__(parent, **kw)
        self._show(_render_label(self._currency, self._pw, self._ph, self._pad,
                                 self._scale, *self._style()))


# ── Fee Block (short wide [ LABEL | value unit ] tile) ───────────────────────
class FeeBlock(_ImagePanel):

    def __init__(self, parent, total_w, scale=1.0, label="LOW", unit="sat/vB",
                 border_hi="#c9a84c", border_lo="#6a5820", finish="Gloss", **kw):
        self._scale = scale
        self._label = label
        self._unit = unit
        self._value = None
        self._value_rgb = None
        self._spark = None
        self._border_hi, self._border_lo, self._finish = border_hi, border_lo, finish
        self._pad = max(3, int(4 * scale))
        self._pw  = max(60, total_w - self._pad * 2)
        self._ph  = max(24, int(BASE_FEE_H * scale))
        super().__init__(parent, **kw)
        self._draw()

    def _draw(self):
        val = str(self._value) if self._value is not None else "--"
        self._show(_render_stat(self._label, val, self._unit, self._pw,
                                self._ph, self._pad, self._scale,
                                *self._style(), value_rgb=self._value_rgb,
                                spark=self._spark))

    def set_value(self, n, unit=None, value_rgb=None, spark=None):
        """Show `n`; redraws only if something on the tile actually changed."""
        unit = self._unit if unit is None else unit
        state = (n, unit, value_rgb, spark)
        if state == (self._value, self._unit, self._value_rgb, self._spark):
            return
        self._value, self._unit, self._value_rgb, self._spark = state
        self._draw()


# ── News Ticker (full-width scrolling headlines) ─────────────────────────────
class NewsTicker(_ImagePanel):
    """[ NEWS | headline · headline · … ] sliding right to left.

    The headlines are one pre-rendered strip image shown twice, end to end;
    scrolling is just moving those two canvas items. Two slices of the card
    sit on top at either end so text slides under the rim, not across it.
    """

    def __init__(self, parent, total_w, scale=1.0, border_hi="#c9a84c",
                 border_lo="#6a5820", finish="Gloss", **kw):
        self._scale = scale
        self._border_hi, self._border_lo, self._finish = border_hi, border_lo, finish
        self._pad = max(3, int(4 * scale))
        self._pw  = max(60, total_w - self._pad * 2)
        self._ph  = max(24, int(BASE_FEE_H * scale))
        super().__init__(parent, **kw)
        self._seq = None
        self._spans = []
        self._strip_w = 0
        self._offset = 0.0
        self._paused = False
        self._last = time.monotonic()
        self._strip_photo = self._cap_l_photo = self._cap_r_photo = None
        # Window the headlines show through: right of the label zone's
        # groove, left of the rim.
        div = _stat_div(self._pw, scale, int(BASE_LABEL_W * scale * 1.2))
        self._div = div
        self._x0 = self._pad + div + max(2, int(2 * scale))
        self._x1 = self._pad + self._pw - max(2, int(3 * scale))
        self._s1 = self.create_image(0, 0, anchor="nw", state="hidden")
        self._s2 = self.create_image(0, 0, anchor="nw", state="hidden")
        self._cap_l = self.create_image(0, 0, anchor="nw")
        self._cap_r = self.create_image(self._x1, 0, anchor="nw")
        self.bind("<Enter>", lambda e: self._pause(True))
        self.bind("<Leave>", lambda e: self._pause(False))
        self._draw_card(empty=True)
        self.after(NEWS_TICK_MS, self._tick)

    def _draw_card(self, empty):
        card = _render_stat("NEWS", "--" if empty else "", "", self._pw,
                            self._ph, self._pad, self._scale, *self._style(),
                            div_px=self._div)
        self._show(card)
        self._cap_l_photo = ImageTk.PhotoImage(card.crop((0, 0, self._x0, card.size[1])))
        self._cap_r_photo = ImageTk.PhotoImage(
            card.crop((self._x1, 0, card.size[0], card.size[1])))
        self.itemconfig(self._cap_l, image=self._cap_l_photo)
        self.itemconfig(self._cap_r, image=self._cap_r_photo)

    def set_items(self, items, seq):
        """Show these (title, url) headlines; `seq` says when the list changed."""
        if seq == self._seq:
            return
        self._seq = seq
        if not items:
            self._spans, self._strip_w = [], 0
            for it in (self._s1, self._s2):
                self.itemconfig(it, state="hidden")
            self._draw_card(empty=True)
            return
        # The strip must be at least as wide as the window, or the second
        # copy would show a gap; repeat a short list until it is.
        window = max(1, self._x1 - self._x0)
        strip, spans = _render_news_strip(items, self._pw, self._ph, self._pad,
                                          self._scale, *self._style())
        reps = -(-window // strip.size[0])
        if reps > 1:
            wide = Image.new("RGB", (strip.size[0] * reps, strip.size[1]))
            base = spans
            spans = []
            for i in range(reps):
                dx = i * strip.size[0]
                wide.paste(strip, (dx, 0))
                spans += [(a + dx, b + dx, u) for a, b, u in base]
            strip = wide
        self._spans, self._strip_w = spans, strip.size[0]
        self._strip_photo = ImageTk.PhotoImage(strip)
        self._draw_card(empty=False)
        for it in (self._s1, self._s2):
            self.itemconfig(it, image=self._strip_photo, state="normal")
        self._offset = 0.0
        self._place()

    def _place(self):
        x = self._x0 + int(self._offset)
        self.coords(self._s1, x, 0)
        self.coords(self._s2, x + self._strip_w, 0)

    def _pause(self, on):
        self._paused = on
        self._last = time.monotonic()

    def _tick(self):
        if not self.winfo_exists():
            return
        now = time.monotonic()
        if self._strip_w and not self._paused:
            # Time-based, so a busy main loop slows the frame rate, not the text.
            self._offset -= NEWS_PX_PER_S * self._scale * min(now - self._last, 0.25)
            if self._offset <= -self._strip_w:
                self._offset += self._strip_w
            self._place()
        self._last = now
        self.after(NEWS_TICK_MS, self._tick)

    def url_at(self, x):
        """The link of the headline under canvas x, or None."""
        if not self._strip_w or not (self._x0 <= x < self._x1):
            return None
        pos = (x - self._x0 - int(self._offset)) % self._strip_w
        for a, b, url in self._spans:
            if a <= pos < b:
                return url
        return None

    def open_at(self, x):
        url = self.url_at(x)
        # Checked when the feed was parsed, and again here: this string came
        # off the network and is about to be handed to the OS.
        if url and url.lower().startswith(("http://", "https://")):
            try:
                webbrowser.open(url)
            except Exception as e:
                _debug_log(f"open headline failed: {type(e).__name__}: {e}")

# ── Main Widget Window ─────────────────────────────────────────────────────────
class Tickoshi(tk.Tk):

    def __init__(self):
        super().__init__()
        self._cfg   = self._load_config()
        self._scale = _clamp_scale(self._cfg.get("scale", SIZE_UNIT))
        self._currency = self._cfg.get("currency", "USD")
        self._topmost = self._cfg.get("topmost", True)
        self._refresh_s = self._cfg.get("refresh_s", 60)
        self._opacity = self._cfg.get("opacity", 0.97)
        # Ordered list of module keys, preserving toggle order for layout.
        raw_mods = self._cfg.get("modules", [])
        seen = set()
        self._modules = []
        for k in raw_mods:
            if k in MODULE_KEYS and k not in seen:
                self._modules.append(k)
                seen.add(k)
        self._border_color = self._cfg.get("border_color", "Gold")
        self._finish = self._cfg.get("finish", FINISHES[0])
        if self._finish not in FINISHES:
            self._finish = FINISHES[0]
        self._flash_enabled = self._cfg.get("flash", True)
        self._block_flash = self._cfg.get("block_flash", True)
        self._locked = self._cfg.get("locked", False)
        self._drag  = None
        self._last_price_str = None
        self._last_display_str = None
        self._prev_price = None        # for flash comparison
        self._num_digits = 5
        self._popup = None
        self._hold_after_id = None     # press-and-hold timer for reordering
        self._hold_xy = (0, 0)
        self._reorder_src = None       # module key being dragged, if any
        self._reorder_tgt = None       # module key it is over
        self._tile_keys = {}
        self._wheel_scale = None       # size a Ctrl+wheel gesture is heading to
        self._wheel_after_id = None
        self._frame_border_id = None   # canvas id of the chassis rim image
        self._frame_layers = None      # rim masks for the current window size
        self._frame_photo = None
        self._pulse_level = 0          # glow level currently painted
        self._pulse_after_id = None    # in-flight pulse animation timer
        self._result_queue = queue.Queue()
        self._fetch_after_id = None    # pending fetch-loop timer
        self._fetch_gen = 0            # latest fetch generation; stale workers no-op
        self._aux_after_id = None      # pending slow-loop timer
        self._aux_gen = 0              # latest slow-loop generation
        with _price_cache_lock:
            self._seen_block_seq = _network_cache["block_seq"]
        self._fetch_retry_s = 0        # backoff after a cycle that got nothing

        # Frameless, always-on-top
        if sys.platform.startswith("linux"):
            # X11: overrideredirect(True) makes the window unmanaged, so the
            # WM ignores -topmost (the widget is stuck above managed windows
            # on most WMs/compositors). Use _NET_WM_WINDOW_TYPE=splash instead
            # for a borderless, WM-managed window where -topmost works.
            self.wm_attributes("-type", "splash")
        else:
            self.overrideredirect(True)
        self.wm_attributes("-topmost", self._topmost)
        self.wm_attributes("-alpha", self._opacity)
        if _CORNER_KEY:
            try:
                self.wm_attributes("-transparentcolor", _CORNER_KEY)
            except tk.TclError:
                pass
        self.configure(bg=C_FACE)

        self._build_ui(self._num_digits)
        self._apply_position()

        # Bindings
        self.bind("<ButtonPress-1>",   self._ds)
        self.bind("<B1-Motion>",       self._dm)
        self.bind("<ButtonRelease-1>", self._de)
        self.bind("<Double-Button-1>", self._copy_to_clipboard)
        self.bind_all("<ButtonPress-3>", self._show_menu)
        self.bind_all("<Control-MouseWheel>", self._on_wheel_resize)
        if sys.platform.startswith("linux"):
            # Tk 8.6 on X11 reports the wheel as buttons 4 and 5.
            self.bind_all("<Control-Button-4>", self._on_wheel_resize)
            self.bind_all("<Control-Button-5>", self._on_wheel_resize)
        if sys.platform == "darwin":
            # macOS: Button-2 fires from some trackpad secondary-click configs;
            # Ctrl-click is the canonical Mac fallback. _show_menu doesn't
            # inspect which button fired, so aliasing both is safe.
            self.bind_all("<ButtonPress-2>", self._show_menu)
            self.bind_all("<Control-Button-1>", self._show_menu)
        self._bind_children()

        self._start_result_poller()
        self._start_live_tick()
        self._fetch_loop()
        self._aux_loop()

        # Start mempool.space WS if any WS-fed module is enabled at startup.
        if self._needs_ws():
            _ws_start()

    # ── Border color helpers ──────────────────────────────────────────────────
    def _bc(self, key="hi"):
        return BORDER_COLORS.get(self._border_color, BORDER_COLORS["Gold"])[key]

    def _needs_ws(self) -> bool:
        # Modules that consume data pushed over the mempool.space WebSocket,
        # plus the new-block flash, which is driven by the same feed. Block
        # age and difficulty arrive ONLY on the socket, so they belong here
        # too: left out, those tiles read "--" unless another tile happened
        # to have opened the connection.
        return self._block_flash or any(
            m in self._modules
            for m in ("fees", "hash", "mempool", "blockage", "diff", "height"))

    # ── Config ────────────────────────────────────────────────────────────────
    def _load_config(self) -> dict:
        path = config_path()
        if not os.path.exists(path):
            return {"x": 100, "y": 100, "scale": SIZE_UNIT, "currency": "USD"}
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"[Tickoshi] Failed to load config {path}: {e}",
                  file=sys.stderr)
            return {"x": 100, "y": 100, "scale": SIZE_UNIT, "currency": "USD"}
        # Migrate removed 30s refresh option up to 1 min.
        if isinstance(cfg.get("refresh_s"), (int, float)):
            cfg["refresh_s"] = max(60, int(cfg["refresh_s"]))
        # Migrate removed XMR currency back to USD.
        if cfg.get("currency") not in CURRENCY_TO_GECKO:
            cfg["currency"] = "USD"
        # Migrate legacy single-view `view_mode` → `modules` list.
        if "modules" not in cfg:
            legacy = cfg.get("view_mode", "Price")
            mapping = {
                "Price":        [],
                "Price+Fees":   ["fees"],
                "Fees":         ["fees"],
                "Price+Sats":   ["sats"],
                "Block Height": ["height"],
                "Halving":      ["halving"],
            }
            cfg["modules"] = mapping.get(legacy, [])
            cfg.pop("view_mode", None)
        return cfg

    def _save_config(self):
        self._cfg["x"]            = self.winfo_x()
        self._cfg["y"]            = self.winfo_y()
        self._cfg["scale"]        = self._scale
        self._cfg["currency"]     = self._currency
        self._cfg["topmost"]      = self._topmost
        self._cfg["refresh_s"]    = self._refresh_s
        self._cfg["opacity"]      = self._opacity
        self._cfg["modules"]      = list(self._modules)
        self._cfg["border_color"] = self._border_color
        self._cfg["finish"]       = self._finish
        self._cfg["flash"]        = self._flash_enabled
        self._cfg["block_flash"]  = self._block_flash
        self._cfg["locked"]       = self._locked
        path = config_path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cfg, f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[Tickoshi] Failed to save config {path}: {e}",
                  file=sys.stderr)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def _apply_position(self):
        x = self._cfg.get("x", 100)
        y = self._cfg.get("y", 100)
        self.geometry(f"+{x}+{y}")

    # ── UI Build ───────────────────────────────────────────────────────────────
    def _build_ui(self, num_digits):
        # A pending pulse holds a stale canvas id — cancel before we rebuild.
        if self._pulse_after_id is not None:
            try:
                self.after_cancel(self._pulse_after_id)
            except Exception:
                pass
            self._pulse_after_id = None
        self._frame_border_id = None
        # The tiles a reorder gesture refers to are about to be destroyed.
        self._end_reorder()
        # Every rebuild changes tile sizes or style; drop the old renders.
        _render_cache.clear()

        self._num_digits = num_digits
        s = self._scale
        fp   = max(4, int(FACE_PAD * s))
        gap  = max(4, int(PANEL_GAP * s))
        # Rows under the price; see the layout walk further down.
        sec_rows = _count_secondary_rows(self._modules)

        for w in self.winfo_children():
            w.destroy()

        pad = max(3, int(4 * s))
        label_w = max(30, int(BASE_LABEL_W * s)) + pad * 2
        sign_w  = max(20, int(BASE_SIGN_W * s)) + pad * 2
        digit_w = max(30, int(BASE_PANEL_W * s)) + pad * 2
        panel_h = max(40, int(BASE_PANEL_H * s)) + pad * 2

        fee_h_full = max(24, int(BASE_FEE_H * s)) + pad * 2
        sec_row_extra = sec_rows * (gap + fee_h_full)

        inner_w = label_w + gap + sign_w + gap + num_digits * digit_w + (num_digits - 1) * gap
        total_w = inner_w + fp * 2
        total_h = panel_h + sec_row_extra + fp * 2

        self._frame_cv = tk.Canvas(self, width=total_w, height=total_h,
                                   bg=C_FACE, highlightthickness=0)
        self._frame_cv.pack()

        # With overrideredirect(True) on Windows and -type=splash on Linux,
        # the WM doesn't always auto-resize the toplevel to match the packed
        # canvas — after a rebuild that grows the layout (fees row or an odd
        # trailing single tile rendering full-width) the window can keep its
        # previous height and crop the top/bottom. Flush any pending
        # pack-driven configure BEFORE pinning the explicit size, then flush
        # again AFTER so our geometry request is the last thing the WM sees.
        # The pre-flush matters on Windows 11, the post-flush on X11.
        self.update_idletasks()
        self.geometry(f"{total_w}x{total_h}")
        self.update_idletasks()

        # Chassis rim. Only the strip outside the tiles shows; the container
        # below covers the middle.
        self._frame_layers = _frame_layers(total_w, total_h, s)
        self._frame_photo = ImageTk.PhotoImage(self._rim_image())
        self._pulse_level = 0
        self._frame_border_id = self._frame_cv.create_image(
            0, 0, anchor="nw", image=self._frame_photo)

        # Vertical container hosts main row + (optional) fee row
        container = tk.Frame(self._frame_cv, bg=C_FACE)
        self._frame_cv.create_window(total_w // 2, total_h // 2,
                                     anchor="center", window=container)

        # Main row
        row = tk.Frame(container, bg=C_FACE)
        row.pack(side="top")

        # Label panel
        self._label_panel = LabelPanel(
            row, scale=s, currency=self._currency,
            border_hi=self._bc("hi"), border_lo=self._bc("lo"),
            finish=self._finish)
        self._label_panel.pack(side="left", padx=(0, gap))

        # Sign panel
        sign = self._get_sign_symbol()
        self._sign_panel = SignPanel(
            row, scale=s, symbol=sign,
            border_hi=self._bc("hi"), border_lo=self._bc("lo"),
            finish=self._finish)
        self._sign_panel.pack(side="left", padx=(0, gap))

        # Digit panels
        self._digit_panels = []
        for i in range(num_digits):
            dp = DigitPanel(row, scale=s,
                            border_hi=self._bc("hi"), border_lo=self._bc("lo"),
                            finish=self._finish)
            dp.pack(side="left", padx=(0, gap if i < num_digits - 1 else 0))
            self._digit_panels.append(dp)

        # Secondary-row panel refs — reset each rebuild.
        self._fee_panels = []
        self._tile_keys = {}           # tile widget -> module key, for reordering
        self._sats_panel = None
        self._height_panel = None
        self._halving_panel = None
        self._hash_panel = None
        self._mempool_panel = None
        self._change_panel = None
        self._diff_panel = None
        self._blockage_panel = None
        self._news_panel = None
        self._chart_panel = None
        self._fng_panel = None
        self._ath_panel = None

        def _new_row():
            r = tk.Frame(container, bg=C_FACE)
            r.pack(side="top", pady=(gap, 0))
            return r

        def _build_fees_row():
            fee_row = _new_row()
            fee_block_total = (inner_w - 2 * gap) // 3
            for i, lbl in enumerate(("LOW", "MED", "HIGH")):
                fb = FeeBlock(fee_row, total_w=fee_block_total, scale=s, label=lbl,
                              border_hi=self._bc("hi"), border_lo=self._bc("lo"),
                              finish=self._finish)
                fb.pack(side="left", padx=(0, gap if i < 2 else 0))
                self._tile_keys[fb] = "fees"
                self._fee_panels.append(fb)
            with _price_cache_lock:
                vals = (_fees_cache.get("low"),
                        _fees_cache.get("med"),
                        _fees_cache.get("high"))
            for panel, v in zip(self._fee_panels, vals):
                panel.set_value(v)

        def _build_news_row():
            self._news_panel = NewsTicker(
                _new_row(), total_w=inner_w, scale=s,
                border_hi=self._bc("hi"), border_lo=self._bc("lo"),
                finish=self._finish)
            self._news_panel.pack(side="left")
            self._tile_keys[self._news_panel] = "news"

        def _build_single_tile(parent, key, width, right_pad):
            sign = CURRENCY_SIGNS.get(self._currency, "$")
            spec = {
                "sats":     ("SATS",   f"per {sign}"),
                "change":   ("24H",    "%"),
                "height":   ("HEIGHT", "blk"),
                "blockage": ("BLOCK",  "ago"),
                "halving":  ("HALVING", "days"),
                "diff":     ("DIFF",   "%"),
                "hash":     ("HASH",   "EH/s"),
                "mempool":  ("MEMP",   "MB"),
                "chart":    ("CHART",  "24h"),
                "fng":      ("F&G",    ""),
                "ath":      ("ATH",    "%"),
            }[key]
            fb = FeeBlock(parent, total_w=width, scale=s,
                          label=spec[0], unit=spec[1],
                          border_hi=self._bc("hi"), border_lo=self._bc("lo"),
                          finish=self._finish)
            fb.pack(side="left", padx=(0, right_pad))
            self._tile_keys[fb] = key
            attr = {"sats": "_sats_panel", "height": "_height_panel",
                    "halving": "_halving_panel", "hash": "_hash_panel",
                    "mempool": "_mempool_panel", "change": "_change_panel",
                    "diff": "_diff_panel", "blockage": "_blockage_panel",
                    "chart": "_chart_panel", "fng": "_fng_panel",
                    "ath": "_ath_panel"}[key]
            setattr(self, attr, fb)

        half_w = (inner_w - gap) // 2
        for keys in _layout_rows(self._modules):
            if keys[0] == "fees":
                _build_fees_row()
            elif keys[0] == "news":
                _build_news_row()
            elif len(keys) == 1:
                # The one tile without a partner: stretch it, don't strand it.
                _build_single_tile(_new_row(), keys[0], inner_w, 0)
            else:
                row = _new_row()
                _build_single_tile(row, keys[0], half_w, gap)
                _build_single_tile(row, keys[1], half_w, 0)

        # Seed secondary panels from caches so rebuilds don't blank them.
        self._update_secondary_panels()

        if self._last_display_str is not None:
            self._set_digits(self._last_display_str)

    def _get_sign_symbol(self) -> str:
        return CURRENCY_SIGNS.get(self._currency, "$")

    def _compute_sats_str(self) -> str:
        gecko_id = CURRENCY_TO_GECKO.get(self._currency, "usd")
        with _price_cache_lock:
            price = _price_cache.get(gecko_id)
        if not price or price <= 0:
            return "--"
        return f"{int(round(100_000_000 / price)):,}"

    def _compute_height_str(self) -> str:
        with _price_cache_lock:
            h = _block_height_cache.get("height")
        return f"{h:,}" if isinstance(h, int) else "--"

    def _compute_halving_str(self) -> str:
        with _price_cache_lock:
            h = _block_height_cache.get("height")
        days = calc_halving_days(h)
        return f"{days:,}" if isinstance(days, int) else "--"

    def _compute_hash_str(self) -> str:
        with _price_cache_lock:
            ehs = _network_cache.get("hashrate_ehs")
        if not isinstance(ehs, (int, float)) or ehs <= 0:
            return "--"
        return f"{ehs:,.0f}" if ehs >= 100 else f"{ehs:,.1f}"

    def _compute_change_str(self) -> str:
        """24h move as a signed percentage. Only CoinGecko supplies this, so it
        reads "--" whenever the Binance fallback provided the price."""
        gecko_id = CURRENCY_TO_GECKO.get(self._currency, "usd")
        with _price_cache_lock:
            pct = _change_cache.get(gecko_id)
        return _fmt_signed_pct(pct)

    def _compute_diff_str(self) -> str:
        """Projected change at the next retarget, signed."""
        with _price_cache_lock:
            pct = _network_cache.get("diff_change_pct")
        return _fmt_signed_pct(pct)

    def _compute_blockage_str(self) -> str:
        """Time since the newest block. Recomputed every second from a stored
        timestamp, so it ticks without needing anything from the network."""
        with _price_cache_lock:
            ts = _network_cache.get("block_ts")
        if not isinstance(ts, (int, float)) or ts <= 0:
            return "--"
        secs = int(time.time() - ts)
        if secs < 0:
            secs = 0          # a block timestamp may sit slightly in the future
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m"
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"

    def _compute_mempool_str(self) -> str:
        with _price_cache_lock:
            mb = _network_cache.get("mempool_mb")
        if not isinstance(mb, (int, float)) or mb < 0:
            return "--"
        return f"{mb:,.0f}" if mb >= 100 else f"{mb:,.1f}"

    def _update_secondary_panels(self):
        if self._sats_panel is not None:
            self._sats_panel.set_value(self._compute_sats_str())
        if self._height_panel is not None:
            self._height_panel.set_value(self._compute_height_str())
        if self._halving_panel is not None:
            self._halving_panel.set_value(self._compute_halving_str())
        if self._hash_panel is not None:
            self._hash_panel.set_value(self._compute_hash_str())
        if self._mempool_panel is not None:
            self._mempool_panel.set_value(self._compute_mempool_str())
        if self._change_panel is not None:
            self._change_panel.set_value(self._compute_change_str())
        if self._diff_panel is not None:
            self._diff_panel.set_value(self._compute_diff_str())
        if self._blockage_panel is not None:
            self._blockage_panel.set_value(self._compute_blockage_str())
        if (self._news_panel is None and self._chart_panel is None
                and self._fng_panel is None and self._ath_panel is None):
            return
        gid = CURRENCY_TO_GECKO.get(self._currency, "usd")
        with _price_cache_lock:
            items, seq = list(_news_cache["items"]), _news_cache["seq"]
            spark = _extras_cache["spark"].get(gid)
            ath = _extras_cache["ath_pct"].get(gid)
            fng, fng_label = _extras_cache["fng"], _extras_cache["fng_label"]
        if self._news_panel is not None:
            self._news_panel.set_items(items, seq)
        if self._chart_panel is not None:
            self._chart_panel.set_value("" if spark else None, spark=spark)
        if self._ath_panel is not None:
            self._ath_panel.set_value(_fmt_signed_pct(ath))
        if self._fng_panel is not None:
            if fng is None:
                self._fng_panel.set_value(None, unit="")
            else:
                self._fng_panel.set_value(str(fng), unit=fng_label,
                                          value_rgb=_fng_rgb(fng))

    # ── Border pulse animation ────────────────────────────────────────────────
    def _pulse(self, color):
        """Flood the chassis rim with `color`, fading back to the theme."""
        if self._frame_border_id is None:
            return
        # Cancel any in-flight pulse so the latest direction wins immediately.
        if self._pulse_after_id is not None:
            try:
                self.after_cancel(self._pulse_after_id)
            except Exception:
                pass
            self._pulse_after_id = None
        self._pulse_step(color, 0)

    def _rim_image(self, pulse=None, amount=0.0):
        return _render_frame(self._frame_layers, self._bc("hi"), self._bc("lo"),
                             self._finish, pulse, amount)

    def _pulse_step(self, color, step):
        if not self.winfo_exists() or self._frame_border_id is None:
            self._pulse_after_id = None
            return

        # Eased fade from the flash colour back to the resting rim. Repaint
        # only when the quantised level moves: most of the 100 steps land on
        # a level already on screen.
        done = step >= PULSE_STEPS
        t = 1.0 if done else _smoothstep(step / PULSE_STEPS)
        level = round((1 - t) * PULSE_LEVELS)
        if level != self._pulse_level or step == 0:
            self._pulse_level = level
            try:
                self._frame_photo.paste(
                    self._rim_image(_rgb(color), level / PULSE_LEVELS))
            except Exception:
                self._pulse_after_id = None
                return
        if done:
            self._pulse_after_id = None
            return

        self._pulse_after_id = self.after(
            PULSE_MS, lambda: self._pulse_step(color, step + 1))

    # ── Display update ────────────────────────────────────────────────────────
    def _update_display(self, display_str: str | None):
        if display_str is None:
            for dp in self._digit_panels:
                dp.set("-")
        else:
            # Flash check (primary display is always price now)
            if self._flash_enabled:
                try:
                    new_val = int(display_str)
                    if self._prev_price is not None:
                        if new_val > self._prev_price:
                            self._pulse("#18c558")        # green = up
                        elif new_val < self._prev_price:
                            self._pulse("#e63b3b")        # red = down
                    self._prev_price = new_val
                except ValueError:
                    pass

            self._last_display_str = display_str
            needed = min(max(len(display_str), 1), MAX_DIGITS)

            if needed != self._num_digits:
                pos_x, pos_y = self.winfo_x(), self.winfo_y()
                self._build_ui(needed)
                self._bind_children()
                self.geometry(f"+{pos_x}+{pos_y}")
            else:
                self._set_digits(display_str)

        # Secondary row updates (each runs only if its module is enabled).
        if self._fee_panels:
            with _price_cache_lock:
                vals = (_fees_cache.get("low"),
                        _fees_cache.get("med"),
                        _fees_cache.get("high"))
            for panel, v in zip(self._fee_panels, vals):
                panel.set_value(v)
        self._update_secondary_panels()

    def _set_digits(self, display_str: str):
        digits = list(display_str[-self._num_digits:])
        while len(digits) < self._num_digits:
            digits.insert(0, "")
        for i, dp in enumerate(self._digit_panels):
            dp.set(digits[i])

    # ── Fetch loop ─────────────────────────────────────────────────────────────
    def _start_result_poller(self):
        """Drain cross-thread results on the main Tk thread."""
        def _poll():
            try:
                while True:
                    fn = self._result_queue.get_nowait()
                    try:
                        fn()
                    except Exception as e:
                        # Swallowed so one bad result can't kill the poller,
                        # but never silently — this is where a render fault
                        # would otherwise vanish without trace.
                        _debug_log(f"result callback failed: "
                                   f"{type(e).__name__}: {e}")
            except queue.Empty:
                pass
            if self.winfo_exists():
                self.after(50, _poll)
        self.after(50, _poll)

    def _start_live_tick(self):
        """Repaint the secondary tiles once a second.

        The block-age counter has to advance on its own, with no new data to
        prompt it. set_value() is a no-op unless the rendered text actually
        changed, so this costs a few string comparisons per second — and it
        also keeps the socket-fed tiles current instead of leaving them until
        the next price cycle, which on a 60-minute interval is an hour away.
        """
        self._tick_error_logged = False

        def _tick():
            if not self.winfo_exists():
                return
            try:
                self._update_secondary_panels()
                self._check_new_block()
            except Exception as e:
                # Cosmetic, and it must not stop the timer or flood a 200-line
                # log at one line a second, so say it once and carry on.
                if not self._tick_error_logged:
                    self._tick_error_logged = True
                    _debug_log(f"tile tick failed: {type(e).__name__}: {e}")
            self.after(1000, _tick)

        self.after(1000, _tick)

    def _check_new_block(self):
        """Flash the rim when a block has been mined since the last look."""
        with _price_cache_lock:
            seq = _network_cache["block_seq"]
        if seq == self._seen_block_seq:
            return
        self._seen_block_seq = seq
        # A price flash already on screen says more; don't cut it short.
        if self._block_flash and self._pulse_after_id is None:
            r, g, b = _mix(_rgb(self._bc("hi")), _WHITE, 0.6)
            self._pulse(f"#{r:02x}{g:02x}{b:02x}")

    # ── Slow loop (news, chart, ATH, Fear & Greed) ────────────────────────────
    # Separate from the price loop on purpose: headlines must not wait out a
    # 60-minute price interval, and nothing that goes wrong here may reach
    # the price loop. Same shape and the same invariant, though — the queued
    # result is what arms the next round, so both halves use `finally`.
    def _aux_loop(self):
        if self._aux_after_id is not None:
            try:
                self.after_cancel(self._aux_after_id)
            except Exception:
                pass
            self._aux_after_id = None
        self._aux_gen += 1
        gen = self._aux_gen
        mods = frozenset(self._modules) & frozenset(AUX_MODULES)
        if not mods:
            return          # nothing to feed; a tile toggle restarts the loop
        currency = self._currency

        def _worker():
            ok = True
            try:
                if "news" in mods:
                    ok = _fetch_news() and ok
                if mods & {"chart", "ath"}:
                    ok = _fetch_market_extras(currency) and ok
                if "fng" in mods:
                    ok = _fetch_fear_greed() and ok
            except Exception as e:
                ok = False
                _debug_log(f"slow-loop worker crashed: {type(e).__name__}: {e}")
            finally:
                self._result_queue.put(lambda: self._on_aux_done(gen, ok))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_aux_done(self, gen, ok):
        if gen != self._aux_gen:
            return
        try:
            self._update_secondary_panels()
        finally:
            if gen == self._aux_gen and self.winfo_exists():
                delay = AUX_REFRESH_S if ok else AUX_RETRY_S
                self._aux_after_id = self.after(delay * 1000, self._aux_loop)

    def _fetch_loop(self):
        # Cancel any pending next-tick timer so we don't stack loops.
        if self._fetch_after_id is not None:
            try:
                self.after_cancel(self._fetch_after_id)
            except Exception:
                pass
            self._fetch_after_id = None

        # Bump the generation so any in-flight worker from a previous call
        # becomes stale and won't reschedule. Without this, menu callbacks
        # that trigger _fetch_loop() while a worker is still running can
        # leak `after` timers and cause double-fetches.
        self._fetch_gen += 1
        gen = self._fetch_gen

        currency = self._currency
        modules = frozenset(self._modules)
        needs_height = ("height" in modules) or ("halving" in modules)

        def _worker():
            # The queued callback is what schedules the next cycle, so it must
            # be posted no matter what happens above it. An exception escaping
            # this thread would stop the widget refreshing for the rest of the
            # session — which looks exactly like "the price stopped working".
            display, refreshed = None, False
            try:
                refreshed = _fetch_all_prices(currency)
                if needs_height:
                    _fetch_block_height()
                if "hash" in modules:
                    _fetch_hashrate()
                # Fees + mempool arrive over the WebSocket — no fetch here.
                display = fetch_price(currency)
                if not refreshed:
                    _debug_log(f"fetch cycle: no live {currency} price from any "
                               f"source (showing "
                               f"{'last known' if display else 'nothing'})")
            except Exception as e:
                _debug_log(f"fetch worker crashed: {type(e).__name__}: {e}")
            finally:
                self._result_queue.put(
                    lambda: self._on_fetch_done(display, currency, modules,
                                                gen, refreshed))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_fetch_done(self, display, currency, modules, gen, refreshed=True):
        # Stale worker (a newer _fetch_loop has superseded this one) — drop it
        # and in particular don't reschedule, or we'd end up with two timers.
        if gen != self._fetch_gen:
            return
        try:
            # Discard stale results from before a currency/module change; that
            # change restarts the loop itself, so this cycle just stops here.
            if (currency == self._currency
                    and modules == frozenset(self._modules)):
                self._last_price_str = display
                self._update_display(display)
        finally:
            # Rescheduling has to survive a render error. The result poller
            # swallows whatever is raised in here, so a single Tcl error
            # escaping this method used to cancel every future refresh.
            if gen == self._fetch_gen and self.winfo_exists():
                self._fetch_after_id = self.after(
                    int(self._next_fetch_delay(refreshed) * 1000),
                    self._fetch_loop)

    def _next_fetch_delay(self, refreshed: bool) -> float:
        """Seconds until the next fetch: the user's interval when the last one
        worked, a short escalating retry when it came back empty."""
        if refreshed:
            self._fetch_retry_s = 0
            return self._refresh_s
        self._fetch_retry_s = min(
            max(self._fetch_retry_s * 2, FETCH_RETRY_MIN_S), FETCH_RETRY_MAX_S)
        # Never retry more slowly than the interval the user actually picked.
        return min(self._fetch_retry_s, self._refresh_s)

    # ── Bindings ──────────────────────────────────────────────────────────────
    def _bind_children(self):
        for c in self.winfo_children():
            self._bind_recursive(c)

    def _bind_recursive(self, w):
        w.bind("<ButtonPress-1>",   self._ds)
        w.bind("<B1-Motion>",       self._dm)
        w.bind("<ButtonRelease-1>", self._de)
        w.bind("<Double-Button-1>", self._copy_to_clipboard)
        for c in w.winfo_children():
            self._bind_recursive(c)

    # ── Drag ──────────────────────────────────────────────────────────────────
    # A plain left-drag moves the window from anywhere. Resting the mouse on
    # a tile for HOLD_MS first turns the drag into "move this tile": drop it
    # on another tile and it takes that tile's place in the order.
    # Every handler here can run twice per event (the widget's own binding
    # and the toplevel's, which children inherit), so each must be safe to
    # repeat.
    def _ds(self, e):
        if self._locked:
            return
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())
        self._cancel_hold()
        if e.widget in self._tile_keys:
            key = self._tile_keys[e.widget]
            self._hold_xy = (e.x_root, e.y_root)
            self._hold_after_id = self.after(
                HOLD_MS, lambda: self._begin_reorder(key))

    def _dm(self, e):
        if self._locked:
            return
        if self._reorder_src is not None:
            self._reorder_over(self.winfo_containing(e.x_root, e.y_root))
            return
        if self._hold_after_id is not None:
            if (abs(e.x_root - self._hold_xy[0]) <= HOLD_SLOP_PX
                    and abs(e.y_root - self._hold_xy[1]) <= HOLD_SLOP_PX):
                return          # still deciding: don't let a tremor move the window
            self._cancel_hold()
        if self._drag:
            self.geometry(f"+{e.x_root-self._drag[0]}+{e.y_root-self._drag[1]}")

    def _de(self, e):
        self._cancel_hold()
        if self._reorder_src is not None:
            src, tgt = self._reorder_src, self._reorder_tgt
            self._end_reorder()
            if tgt and tgt != src and src in self._modules and tgt in self._modules:
                self._move_module(src, tgt)
            return
        if self._drag:
            self._save_config()
        self._drag = None

    def _cancel_hold(self):
        if self._hold_after_id is not None:
            try:
                self.after_cancel(self._hold_after_id)
            except Exception:
                pass
            self._hold_after_id = None

    def _mark_tiles(self, key, color):
        for w, k in self._tile_keys.items():
            if k == key and w.winfo_exists():
                w.set_mark(color)

    def _begin_reorder(self, key):
        self._hold_after_id = None
        if self._locked or key not in self._modules:
            return
        self._drag = None               # this gesture no longer moves the window
        self._reorder_src, self._reorder_tgt = key, None
        self._mark_tiles(key, self._bc("hi"))
        self.config(cursor="fleur")

    def _reorder_over(self, widget):
        key = self._tile_keys.get(widget)
        if key == self._reorder_src:
            key = None
        if key == self._reorder_tgt:
            return
        if self._reorder_tgt:
            self._mark_tiles(self._reorder_tgt, None)
        self._reorder_tgt = key
        if key:
            self._mark_tiles(key, "#ffffff")

    def _end_reorder(self):
        self._cancel_hold()
        if self._reorder_src is None:
            return
        for key in (self._reorder_src, self._reorder_tgt):
            if key:
                self._mark_tiles(key, None)
        self._reorder_src = self._reorder_tgt = None
        self.config(cursor="")

    def _move_module(self, src, tgt):
        """Move tile `src` to where it was dropped, on tile `tgt`."""
        full = FULL_ROW_MODULES
        if src not in full and tgt in full:
            # A half-width tile dropped on a full-row one: the only thing
            # that can change on screen is which side of the tile's row the
            # full row sits, so move the full row past it.
            src, tgt = tgt, src
        rows = _layout_rows(self._modules)
        if src in full:
            # A full-row tile moves by whole rows. A list position between
            # the two tiles of a pair is not a place on screen: dropped
            # there, the pair simply re-formed below it and nothing moved.
            si = next(i for i, r in enumerate(rows) if src in r)
            ti = next(i for i, r in enumerate(rows) if tgt in r)
            row = rows.pop(si)
            rows.insert(ti, row)    # after tgt's row going down, before it going up
            order = [k for r in rows for k in r]
        else:
            order = [k for r in rows for k in r]
            i, j = order.index(src), order.index(tgt)
            order.pop(i)
            order.insert(j, src)
        # Keep the stored order equal to the order on screen.
        order = [k for r in _layout_rows(order) for k in r]
        if order == self._modules:
            return
        self._modules = order
        pos_x, pos_y = self.winfo_x(), self.winfo_y()
        self._build_ui(self._num_digits)
        self._bind_children()
        self.geometry(f"+{pos_x}+{pos_y}")
        self._save_config()

    # ── Copy to clipboard ────────────────────────────────────────────────────
    def _copy_to_clipboard(self, e):
        # Every child gets this binding, the ticker included; there a
        # double-click means "open this headline", not "copy the price".
        if isinstance(e.widget, NewsTicker):
            e.widget.open_at(e.x)
            return
        if self._last_display_str:
            self.clipboard_clear()
            self.clipboard_append(self._last_display_str)
            # On Linux/X11, Tk clipboard ownership is held by the process and
            # lost when the app exits unless the event loop has pumped at
            # least once after clipboard_append. Force it so a double-click
            # → close → paste round-trip still works.
            try:
                self.update()
            except Exception:
                pass

    # ── Right-click menu ──────────────────────────────────────────────────────
    def _show_menu(self, e):
        if self._popup is not None:
            try:
                self._popup.unpost()
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None
        try:
            self.grab_release()
        except Exception:
            pass

        menu_style = dict(tearoff=0, bg="#1a1a1a", fg="#ffffff",
                          activebackground="#333333", activeforeground="#ffffff",
                          bd=0, relief="flat")
        menu = tk.Menu(self, **menu_style)
        self._popup = menu

        # ── Cascades (alphabetical) ───────────────────────────────────────
        # Border
        brd_menu = tk.Menu(menu, **menu_style)
        for name in BORDER_COLORS:
            check = " \u2713" if name == self._border_color else ""
            brd_menu.add_command(label=f"  {name}{check}",
                                command=lambda n=name: self._menu_set_border(n))
        menu.add_cascade(label="  Border", menu=brd_menu)

        # Currency
        curr_menu = tk.Menu(menu, **menu_style)
        for code, _, _ in sorted(CURRENCIES, key=lambda c: c[0]):
            check = " \u2713" if code == self._currency else ""
            curr_menu.add_command(label=f"  {code}{check}",
                                 command=lambda c=code: self._menu_set_currency(c))
        menu.add_cascade(label="  Currency", menu=curr_menu)

        # Finish
        fin_menu = tk.Menu(menu, **menu_style)
        for name in FINISHES:
            check = " \u2713" if name == self._finish else ""
            fin_menu.add_command(label=f"  {name}{check}",
                                command=lambda n=name: self._menu_set_finish(n))
        menu.add_cascade(label="  Finish", menu=fin_menu)

        # Opacity
        opa_menu = tk.Menu(menu, **menu_style)
        for label, val in OPACITY_OPTIONS:
            check = " \u2713" if abs(self._opacity - val) < 0.01 else ""
            opa_menu.add_command(label=f"  {label}{check}",
                                command=lambda v=val: self._menu_set_opacity(v))
        menu.add_cascade(label="  Opacity", menu=opa_menu)

        # Refresh
        ref_menu = tk.Menu(menu, **menu_style)
        for label, secs in REFRESH_OPTIONS:
            check = " \u2713" if self._refresh_s == secs else ""
            ref_menu.add_command(label=f"  {label}{check}",
                                command=lambda s=secs: self._menu_set_refresh(s))
        menu.add_cascade(label="  Refresh", menu=ref_menu)

        # Size
        size_menu = tk.Menu(menu, **menu_style)
        cur_pct = _scale_to_pct(self._scale)
        for pct in SIZE_PRESETS:
            check = " \u2713" if pct == cur_pct else ""
            size_menu.add_command(label=f"  {pct}%{check}",
                                 command=lambda p=pct: self._menu_set_size(_pct_to_scale(p)))
        if cur_pct not in SIZE_PRESETS:
            # Reached with Ctrl + wheel; show it so the menu never has no tick.
            size_menu.add_separator()
            size_menu.add_command(label=f"  {cur_pct}% \u2713", state="disabled")
        menu.add_cascade(label="  Size", menu=size_menu)

        # View (multi-select toggles for secondary-row modules)
        view_menu = tk.Menu(menu, **menu_style)
        for key, label in sorted(MODULES, key=lambda km: km[1].lower()):
            check = " \u2713" if key in self._modules else ""
            view_menu.add_command(label=f"  {label}{check}",
                                 command=lambda k=key: self._menu_toggle_module(k))
        menu.add_cascade(label="  View", menu=view_menu)

        # ── Toggles (pinned, not alphabetized) ────────────────────────────
        menu.add_separator()

        top_check = " \u2713" if self._topmost else ""
        menu.add_command(label=f"  Always on top{top_check}",
                         command=self._menu_toggle_topmost)

        flash_check = " \u2713" if self._flash_enabled else ""
        menu.add_command(label=f"  Price flash{flash_check}",
                         command=self._menu_toggle_flash)

        blk_check = " \u2713" if self._block_flash else ""
        menu.add_command(label=f"  Block flash{blk_check}",
                         command=self._menu_toggle_block_flash)

        lock_check = " \u2713" if self._locked else ""
        menu.add_command(label=f"  Lock{lock_check}",
                         command=self._menu_toggle_lock)

        # Read the OS every time the menu opens rather than trusting a stored
        # flag, so an entry removed behind our back shows as off.
        auto_check = " \u2713" if autostart_enabled() else ""
        menu.add_command(label=f"  Start at login{auto_check}",
                         command=self._menu_toggle_autostart)

        menu.add_separator()
        menu.add_command(label="  Close", command=self._menu_quit)

        menu.tk_popup(e.x_root, e.y_root)
        menu.bind("<Unmap>", self._on_popup_close)
        return "break"

    def _on_popup_close(self, _event=None):
        self._popup = None
        try:
            self.grab_release()
        except Exception:
            pass

    def _menu_set_size(self, scale):
        self.after(30, lambda: self._do_set_size(scale))

    def _on_wheel_resize(self, e):
        """Ctrl + wheel: resize in SIZE_STEP steps, anywhere in the range."""
        if self._locked:
            return "break"
        up = e.delta > 0 if e.delta else getattr(e, "num", 0) == 4
        base = self._wheel_scale if self._wheel_scale is not None else self._scale
        # Step to the next multiple of SIZE_STEP, so a size that is not on the
        # grid (one saved before the percentages were recalibrated, say)
        # lands on it with the first notch.
        pct = _scale_to_pct(base)
        pct = (pct // SIZE_STEP + 1) * SIZE_STEP if up else -(-pct // SIZE_STEP) * SIZE_STEP - SIZE_STEP
        target = _clamp_scale(_pct_to_scale(pct))
        if target == base:
            return "break"
        # A rebuild re-renders every tile, so wait for the wheel to settle
        # instead of rebuilding once per notch.
        self._wheel_scale = target
        if self._wheel_after_id is not None:
            self.after_cancel(self._wheel_after_id)
        self._wheel_after_id = self.after(140, self._apply_wheel_resize)
        return "break"

    def _apply_wheel_resize(self):
        self._wheel_after_id = None
        target, self._wheel_scale = self._wheel_scale, None
        if target is not None and target != self._scale:
            self._do_set_size(target)

    def _do_set_size(self, scale):
        self._scale = _clamp_scale(scale)
        pos_x, pos_y = self.winfo_x(), self.winfo_y()
        self._build_ui(self._num_digits)
        self._bind_children()
        self.geometry(f"+{pos_x}+{pos_y}")
        self._save_config()

    def _menu_set_currency(self, code):
        if code == self._currency:
            return
        self.after(30, lambda: self._do_set_currency(code))

    def _do_set_currency(self, code):
        self._currency = code
        self._last_price_str = None
        self._last_display_str = None
        self._prev_price = None
        pos_x, pos_y = self.winfo_x(), self.winfo_y()
        self._build_ui(self._num_digits)
        self._bind_children()
        self.geometry(f"+{pos_x}+{pos_y}")
        self._save_config()
        for dp in self._digit_panels:
            dp.set("-")
        self._fetch_loop()
        self._aux_loop()

    def _menu_set_refresh(self, secs):
        self._refresh_s = secs
        self._save_config()
        # Restart loop so the new interval takes effect immediately.
        self._fetch_loop()

    def _menu_set_opacity(self, val):
        self._opacity = val
        self.wm_attributes("-alpha", val)
        self._save_config()

    def _menu_toggle_module(self, key):
        if key not in MODULE_KEYS:
            return
        self.after(30, lambda: self._do_toggle_module(key))

    def _do_toggle_module(self, key):
        if key in self._modules:
            self._modules.remove(key)
        else:
            self._modules.append(key)
        pos_x, pos_y = self.winfo_x(), self.winfo_y()
        self._build_ui(self._num_digits)
        self._bind_children()
        self.geometry(f"+{pos_x}+{pos_y}")
        self._save_config()
        if self._needs_ws():
            _ws_start()
        else:
            _ws_stop()
        self._fetch_loop()
        self._aux_loop()

    def _menu_set_border(self, name):
        if name == self._border_color:
            return
        self.after(30, lambda: self._do_set_border(name))

    def _do_set_border(self, name):
        self._border_color = name
        pos_x, pos_y = self.winfo_x(), self.winfo_y()
        self._build_ui(self._num_digits)
        self._bind_children()
        self.geometry(f"+{pos_x}+{pos_y}")
        self._save_config()

    def _menu_set_finish(self, name):
        if name == self._finish:
            return
        self.after(30, lambda: self._do_set_finish(name))

    def _do_set_finish(self, name):
        self._finish = name
        pos_x, pos_y = self.winfo_x(), self.winfo_y()
        self._build_ui(self._num_digits)
        self._bind_children()
        self.geometry(f"+{pos_x}+{pos_y}")
        self._save_config()

    def _menu_toggle_topmost(self):
        self._topmost = not self._topmost
        self.wm_attributes("-topmost", self._topmost)
        self._save_config()

    def _menu_toggle_flash(self):
        self._flash_enabled = not self._flash_enabled
        self._save_config()

    def _menu_toggle_block_flash(self):
        self._block_flash = not self._block_flash
        self._save_config()
        # The flash is fed by the WebSocket; it may be the only thing using it.
        if self._needs_ws():
            _ws_start()
        else:
            _ws_stop()

    def _menu_toggle_autostart(self):
        want = not autostart_enabled()
        got = set_autostart(want)
        _debug_log(f"autostart {'enabled' if got else 'disabled'}"
                   + ("" if got == want else "  (requested "
                      f"{'enable' if want else 'disable'}, and it did not take)"))

    def _menu_toggle_lock(self):
        self._locked = not self._locked
        if self._locked:
            self._drag = None
        self._save_config()

    def _menu_quit(self):
        _ws_stop()
        self.after(30, lambda: (self._save_config(), self.destroy()))

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = Tickoshi()
    app.mainloop()
