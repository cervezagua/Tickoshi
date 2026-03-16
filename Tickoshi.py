"""
Tickoshi — BlockClock Mini style desktop widget
Live Bitcoin price, block height, halving countdown.
Frameless, always-on-top, draggable.
"""

import tkinter as tk
import threading
import json
import os
import math
import urllib.request
import urllib.error

# ── Constants ────────────────────────────────────────────────────────────────
APP_NAME   = "Tickoshi"
MAX_DIGITS = 9           # max digit panels (up to 999,999,999)

# Refresh interval presets: label → seconds
REFRESH_OPTIONS = [
    ("30 sec",  30),
    ("1 min",   60),
    ("5 min",   300),
    ("15 min",  900),
    ("30 min",  1800),
    ("1 hr",    3600),
]

DRUM_STEPS = 12
DRUM_MS    = 14

# Size presets: name → scale factor
SIZES = {"Small": 0.7, "Medium": 1.0, "Large": 1.4}

# Currencies: code → (Binance symbol, CoinGecko id)
CURRENCIES = [
    ("USD", "BTCUSDT", "usd"),
    ("TRY", "BTCTRY",  "try"),
    ("EUR", "BTCEUR",  "eur"),
    ("GBP", "BTCGBP",  "gbp"),
    ("JPY", "BTCJPY",  "jpy"),
    ("RUB", "BTCRUB",  "rub"),
]

# Currency symbols
CURRENCY_SIGNS = {
    "USD": "$", "TRY": "\u20ba", "EUR": "\u20ac",
    "GBP": "\u00a3", "JPY": "\u00a5", "RUB": "\u20bd",
}

# View modes
VIEW_MODES = ["Price", "Block Height", "Halving"]

# Opacity presets
OPACITY_OPTIONS = [("50%", 0.5), ("70%", 0.7), ("85%", 0.85), ("100%", 1.0)]

# Border color themes
BORDER_COLORS = {
    "Gold":   {"hi": "#c9a84c", "lo": "#6a5820", "line": "#c9a84c"},
    "Orange": {"hi": "#e88a2d", "lo": "#7a4a18", "line": "#e88a2d"},
    "White":  {"hi": "#cccccc", "lo": "#666666", "line": "#cccccc"},
}

# Next Bitcoin halving block
NEXT_HALVING_BLOCK = 1_050_000

# Spark chase animation
SPARK_SEGMENTS = 48      # number of border segments
SPARK_TRAIL    = 8       # trail length in segments
SPARK_MS       = 16      # ms per step (~768ms full loop)

# Panel base dimensions (at scale 1.0)
BASE_PANEL_W = 80
BASE_PANEL_H = 110
BASE_SIGN_W  = 50         # currency sign panel (narrower)
BASE_FS      = 62          # digit font size
BASE_R       = 10          # card corner radius

# Layout
FACE_PAD     = 6           # padding inside gold border
PANEL_GAP    = 8           # gap between panels

# Label panel
BASE_LABEL_W  = 78
BASE_LABEL_FS = 16

# ── Colors ───────────────────────────────────────────────────────────────────
C_FACE       = "#080808"   # face background
C_PANEL_BG   = "#0c0c0c"   # panel card background
C_DIGIT      = "#ffffff"   # active digit
C_LABEL_TXT  = "#ffffff"   # "BTC/USD" label text

# ── Helpers ───────────────────────────────────────────────────────────────────
def _darken(hex_c: str, f: float) -> str:
    h = hex_c.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"

def _lerp(a: str, b: str, t: float) -> str:
    ah, bh = a.lstrip("#"), b.lstrip("#")
    ra, ga, ba = int(ah[0:2],16), int(ah[2:4],16), int(ah[4:6],16)
    rb, gb, bb = int(bh[0:2],16), int(bh[2:4],16), int(bh[4:6],16)
    return f"#{int(ra+(rb-ra)*t):02x}{int(ga+(gb-ga)*t):02x}{int(ba+(bb-ba)*t):02x}"

def _smoothstep(t: float) -> float:
    return t * t * (3 - 2 * t)

def config_path() -> str:
    if os.name == "nt":                        # Windows
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:                                      # Linux / macOS
        base = os.environ.get("XDG_CONFIG_HOME",
                              os.path.join(os.path.expanduser("~"), ".config"))
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "tickoshi_config.json")

def _rr_pts(x1, y1, x2, y2, r):
    r = min(r, (x2-x1)//2, (y2-y1)//2)
    return [
        x1+r,y1, x2-r,y1, x2,y1,   x2,y1+r,
        x2,y2-r, x2,y2,   x2-r,y2, x1+r,y2,
        x1,y2,   x1,y2-r, x1,y1+r, x1,y1,
    ]

def _perimeter_points(x1, y1, x2, y2, r, n):
    """Generate n evenly-spaced points clockwise around a rounded rectangle."""
    r = min(r, (x2-x1)//2, (y2-y1)//2)
    # Build path: straight edges + quarter-circle corners
    # Corners: top-right, bottom-right, bottom-left, top-left
    path = []
    # Top edge (left to right)
    path.append(("line", x1+r, y1, x2-r, y1))
    # Top-right corner
    path.append(("arc", x2-r, y1+r, r, -math.pi/2, 0))
    # Right edge (top to bottom)
    path.append(("line", x2, y1+r, x2, y2-r))
    # Bottom-right corner
    path.append(("arc", x2-r, y2-r, r, 0, math.pi/2))
    # Bottom edge (right to left)
    path.append(("line", x2-r, y2, x1+r, y2))
    # Bottom-left corner
    path.append(("arc", x1+r, y2-r, r, math.pi/2, math.pi))
    # Left edge (bottom to top)
    path.append(("line", x1, y2-r, x1, y1+r))
    # Top-left corner
    path.append(("arc", x1+r, y1+r, r, math.pi, 3*math.pi/2))

    # Calculate total perimeter length
    lengths = []
    for seg in path:
        if seg[0] == "line":
            _, lx1, ly1, lx2, ly2 = seg
            lengths.append(math.hypot(lx2-lx1, ly2-ly1))
        else:
            _, _, _, rad, _, _ = seg
            lengths.append(rad * math.pi / 2)  # quarter circle
    total = sum(lengths)

    # Sample n points
    points = []
    for i in range(n):
        target = (i / n) * total
        accum = 0.0
        for j, seg in enumerate(path):
            seg_len = lengths[j]
            if accum + seg_len >= target or j == len(path) - 1:
                t = (target - accum) / seg_len if seg_len > 0 else 0
                t = max(0.0, min(1.0, t))
                if seg[0] == "line":
                    _, lx1, ly1, lx2, ly2 = seg
                    px = lx1 + (lx2 - lx1) * t
                    py = ly1 + (ly2 - ly1) * t
                else:
                    _, cx, cy, rad, a_start, a_end = seg
                    angle = a_start + (a_end - a_start) * t
                    px = cx + rad * math.cos(angle)
                    py = cy + rad * math.sin(angle)
                points.append((px, py))
                break
            accum += seg_len
    return points

# ── FlipCard ──────────────────────────────────────────────────────────────────
class FlipCard(tk.Frame):

    def __init__(self, parent, text_color=C_DIGIT, card_color=C_PANEL_BG,
                 scale=1.0, **kw):
        super().__init__(parent, bg=C_FACE, **kw)
        self.text_color = text_color
        self.card_color = card_color
        self._cur  = ""
        self._nxt  = ""
        self._busy = False
        self._apply_scale(scale)
        self._build_canvases()
        self._draw_static(self._cur)

    def _apply_scale(self, scale):
        self.scale = scale
        self.W   = max(30, int(BASE_PANEL_W * scale))
        self.H   = max(40, int(BASE_PANEL_H * scale))
        self.MID = self.H // 2
        self.R   = max(4,  int(BASE_R  * scale))
        self.FS  = max(12, int(BASE_FS * scale))

    def _build_canvases(self):
        for w in self.winfo_children():
            w.destroy()
        self._top = tk.Canvas(self, width=self.W, height=self.MID,
                               bg=C_FACE, highlightthickness=0)
        self._top.pack(side="top")
        self._bot = tk.Canvas(self, width=self.W, height=self.MID,
                               bg=C_FACE, highlightthickness=0)
        self._bot.pack(side="top")

    def _rr(self, cv, x1, y1, x2, y2, **kw):
        r = min(self.R, (x2-x1)//2, (y2-y1)//2)
        pts = [
            x1+r,y1, x2-r,y1, x2,y1,   x2,y1+r,
            x2,y2-r, x2,y2,   x2-r,y2, x1+r,y2,
            x1,y2,   x1,y2-r, x1,y1+r, x1,y1,
        ]
        return cv.create_polygon(pts, smooth=True, **kw)

    def _draw_half(self, cv, is_top, strips):
        cv.delete("all")
        W, MID, H = self.W, self.MID, self.H
        cc   = self.card_color
        FONT = ("Segoe UI", self.FS, "bold")

        if is_top:
            self._rr(cv, 0, 0, W, H, fill=cc, outline="")
        else:
            self._rr(cv, 0, -MID, W, MID, fill=cc, outline="")

        for digit, y_off, alpha in strips:
            if alpha < 0.02:
                continue
            tc     = _darken(self.text_color, max(0.0, alpha))
            bg_mix = _lerp(cc, "#000000", max(0.0, 1.0 - alpha))

            cy = (MID + y_off) if is_top else (0 + y_off)

            if alpha < 0.92:
                band_h = max(4, int(H * 0.45))
                by1 = max(0, cy - band_h // 2)
                by2 = min(MID, cy + band_h // 2)
                if by2 > by1:
                    cv.create_rectangle(0, by1, W, by2, fill=bg_mix, outline="")

            if digit:
                cv.create_text(W//2, cy, text=digit, font=FONT, fill=tc, anchor="center")

    def _draw_static(self, d):
        self._draw_half(self._top, True,  [(d, 0, 1.0)])
        self._draw_half(self._bot, False, [(d, 0, 1.0)])

    def set(self, digit: str):
        if digit == self._cur:
            return
        if self._busy:
            self._nxt = digit
            return
        self._nxt  = digit
        self._busy = True
        self._drum(0)

    def rebuild(self, scale):
        old = self._cur
        self._apply_scale(scale)
        self._build_canvases()
        self._cur = old
        self._draw_static(self._cur)

    def _drum(self, step):
        t   = _smoothstep(step / DRUM_STEPS)
        H   = self.H

        old_off   = int(-H * t)
        nxt_off   = int( H * (1.0 - t))
        old_alpha = max(0.0, 1.0 - t * 1.4)
        nxt_alpha = max(0.0, (t - 0.3) / 0.7)

        strips = []
        if old_alpha > 0.02: strips.append((self._cur, old_off, old_alpha))
        if nxt_alpha > 0.02: strips.append((self._nxt, nxt_off, nxt_alpha))

        self._draw_half(self._top, True,  strips)
        self._draw_half(self._bot, False, strips)

        if step < DRUM_STEPS:
            self.after(DRUM_MS, lambda: self._drum(step + 1) if self.winfo_exists() else None)
        else:
            self._cur  = self._nxt
            self._busy = False
            if self.winfo_exists():
                self._draw_static(self._cur)

# ── Price API ─────────────────────────────────────────────────────────────────
_price_cache = {}
_block_height_cache = {"height": None}
_price_cache_lock = threading.Lock()

def _fetch_all_prices():
    gecko_ids = ",".join(gid for _, _, gid in CURRENCIES)
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=" + gecko_ids
        req = urllib.request.Request(url, headers={"User-Agent": "Tickoshi/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        with _price_cache_lock:
            for _, _, gid in CURRENCIES:
                if gid in data.get("bitcoin", {}):
                    _price_cache[gid] = float(data["bitcoin"][gid])
        return True
    except Exception:
        pass
    for code, bsym, gid in CURRENCIES:
        try:
            url = "https://api.binance.com/api/v3/ticker/price?symbol=" + bsym
            req = urllib.request.Request(url, headers={"User-Agent": "Tickoshi/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
            with _price_cache_lock:
                _price_cache[gid] = float(data["price"])
        except Exception:
            continue
    return bool(_price_cache)

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

def fetch_price(currency: str = "USD") -> str | None:
    gecko_id = "usd"
    for code, _, gid in CURRENCIES:
        if code == currency:
            gecko_id = gid
            break
    with _price_cache_lock:
        price = _price_cache.get(gecko_id)
    if price is not None:
        return str(int(round(price)))
    return None

def fetch_block_height_str() -> str | None:
    with _price_cache_lock:
        h = _block_height_cache.get("height")
    if h is not None:
        return str(h)
    return None

def calc_halving_days(height: int) -> int | None:
    if height is None or height >= NEXT_HALVING_BLOCK:
        return None
    blocks_remaining = NEXT_HALVING_BLOCK - height
    minutes_remaining = blocks_remaining * 10
    return int(minutes_remaining / 60 / 24)

# ── Digit Panel (FlipCard + gold border) ─────────────────────────────────────
class DigitPanel(tk.Canvas):

    def __init__(self, parent, scale=1.0, border_hi="#c9a84c", border_lo="#6a5820", **kw):
        self._scale = scale
        self._border_hi = border_hi
        self._border_lo = border_lo
        pw = max(30, int(BASE_PANEL_W * scale))
        ph = max(40, int(BASE_PANEL_H * scale))
        pad = max(3, int(4 * scale))
        super().__init__(parent, width=pw + pad*2, height=ph + pad*2,
                         bg=C_FACE, highlightthickness=0, **kw)
        self._pad = pad
        self._pw  = pw
        self._ph  = ph
        self.card = FlipCard(self, scale=scale,
                             text_color=C_DIGIT, card_color=C_PANEL_BG)
        self.create_window(pad, pad, anchor="nw", window=self.card)
        self._draw_border()

    def _draw_border(self):
        pad = self._pad
        pw, ph = self._pw, self._ph
        r = max(4, int(BASE_R * self._scale))
        self._draw_rr(1, 1, pw+pad*2-2, ph+pad*2-2, r+2,
                      outline=self._border_lo, width=1)
        self._draw_rr(pad-2, pad-2, pw+pad+2, ph+pad+2, r+1,
                      outline=self._border_hi, width=1)

    def _draw_rr(self, x1, y1, x2, y2, r, **kw):
        r = min(r, (x2-x1)//2, (y2-y1)//2)
        pts = [
            x1+r,y1, x2-r,y1, x2,y1,   x2,y1+r,
            x2,y2-r, x2,y2,   x2-r,y2, x1+r,y2,
            x1,y2,   x1,y2-r, x1,y1+r, x1,y1,
        ]
        self.create_polygon(pts, smooth=True, fill="", **kw)

    def set(self, digit: str):
        self.card.set(digit)

    def rebuild(self, scale, border_hi=None, border_lo=None):
        self._scale = scale
        if border_hi: self._border_hi = border_hi
        if border_lo: self._border_lo = border_lo
        pw = max(30, int(BASE_PANEL_W * scale))
        ph = max(40, int(BASE_PANEL_H * scale))
        pad = max(3, int(4 * scale))
        self._pad, self._pw, self._ph = pad, pw, ph
        self.config(width=pw+pad*2, height=ph+pad*2)
        self.delete("all")
        self.card.rebuild(scale)
        self.create_window(pad, pad, anchor="nw", window=self.card)
        self._draw_border()

# ── Sign Panel (currency symbol + gold border) ──────────────────────────────
class SignPanel(tk.Canvas):

    def __init__(self, parent, scale=1.0, symbol="$",
                 border_hi="#c9a84c", border_lo="#6a5820", **kw):
        self._scale = scale
        self._symbol = symbol
        self._border_hi = border_hi
        self._border_lo = border_lo
        pw = max(20, int(BASE_SIGN_W * scale))
        ph = max(40, int(BASE_PANEL_H * scale))
        pad = max(3, int(4 * scale))
        super().__init__(parent, width=pw + pad*2, height=ph + pad*2,
                         bg=C_FACE, highlightthickness=0, **kw)
        self._pad, self._pw, self._ph = pad, pw, ph
        self._draw()

    def _draw(self):
        self.delete("all")
        pad = self._pad
        pw, ph = self._pw, self._ph
        r  = max(4, int(BASE_R * self._scale))
        fs = max(12, int(BASE_FS * self._scale * 0.7))

        # Card background
        self._draw_rr(pad, pad, pad+pw, pad+ph, r, fill=C_PANEL_BG, outline="")

        # Border
        self._draw_rr(1, 1, pw+pad*2-2, ph+pad*2-2, r+2,
                      outline=self._border_lo, width=1)
        self._draw_rr(pad-2, pad-2, pw+pad+2, ph+pad+2, r+1,
                      outline=self._border_hi, width=1)

        # Symbol text
        cx = pad + pw // 2
        cy = pad + ph // 2
        font = ("Segoe UI", fs, "bold")
        self.create_text(cx, cy, text=self._symbol, font=font,
                         fill=C_DIGIT, anchor="center")

    def _draw_rr(self, x1, y1, x2, y2, r, **kw):
        r = min(r, (x2-x1)//2, (y2-y1)//2)
        pts = [
            x1+r,y1, x2-r,y1, x2,y1,   x2,y1+r,
            x2,y2-r, x2,y2,   x2-r,y2, x1+r,y2,
            x1,y2,   x1,y2-r, x1,y1+r, x1,y1,
        ]
        self.create_polygon(pts, smooth=True, **kw)

    def rebuild(self, scale, symbol=None, border_hi=None, border_lo=None):
        self._scale = scale
        if symbol is not None:
            self._symbol = symbol
        if border_hi: self._border_hi = border_hi
        if border_lo: self._border_lo = border_lo
        pw = max(20, int(BASE_SIGN_W * scale))
        ph = max(40, int(BASE_PANEL_H * scale))
        pad = max(3, int(4 * scale))
        self._pad, self._pw, self._ph = pad, pw, ph
        self.config(width=pw+pad*2, height=ph+pad*2)
        self._draw()

# ── Label Panel ───────────────────────────────────────────────────────────────
class LabelPanel(tk.Canvas):

    def __init__(self, parent, scale=1.0, currency="USD", view_mode="Price",
                 border_hi="#c9a84c", border_lo="#6a5820", line_color="#c9a84c", **kw):
        self._scale = scale
        self._currency = currency
        self._view_mode = view_mode
        self._border_hi = border_hi
        self._border_lo = border_lo
        self._line_color = line_color
        pw = max(30, int(BASE_LABEL_W * scale))
        ph = max(40, int(BASE_PANEL_H * scale))
        pad = max(3, int(4 * scale))
        super().__init__(parent, width=pw + pad*2, height=ph + pad*2,
                         bg=C_FACE, highlightthickness=0, **kw)
        self._pad, self._pw, self._ph = pad, pw, ph
        self._draw()

    def _draw(self):
        self.delete("all")
        pad = self._pad
        pw, ph = self._pw, self._ph
        r   = max(4, int(BASE_R * self._scale))
        fs  = max(8, int(BASE_LABEL_FS * self._scale))
        cx  = pad + pw // 2
        cy  = pad + ph // 2

        # Card background
        self._rr(pad, pad, pad+pw, pad+ph, r, fill=C_PANEL_BG, outline="")

        # Border
        self._rr(1, 1, pw+pad*2-2, ph+pad*2-2, r+2, outline=self._border_lo, width=1)
        self._rr(pad-2, pad-2, pw+pad+2, ph+pad+2, r+1, outline=self._border_hi, width=1)

        font_bold = ("Segoe UI", fs, "bold")

        if self._view_mode == "Price":
            top_text, bot_text = "BTC", self._currency
        elif self._view_mode == "Block Height":
            top_text, bot_text = "BLOCK", "HEIGHT"
        elif self._view_mode == "Halving":
            top_text, bot_text = "HALV", "DAYS"
        else:
            top_text, bot_text = "BTC", self._currency

        self.create_text(cx, cy - int(ph * 0.15), text=top_text,
                         font=font_bold, fill=C_LABEL_TXT, anchor="center")
        lw = int(pw * 0.6)
        self.create_line(cx - lw//2, cy + int(ph * 0.03),
                         cx + lw//2, cy + int(ph * 0.03),
                         fill=self._line_color, width=max(1, int(1.5 * self._scale)))
        self.create_text(cx, cy + int(ph * 0.22), text=bot_text,
                         font=font_bold, fill=C_LABEL_TXT, anchor="center")

    def _rr(self, x1, y1, x2, y2, r, **kw):
        r = min(r, (x2-x1)//2, (y2-y1)//2)
        pts = [
            x1+r,y1, x2-r,y1, x2,y1,   x2,y1+r,
            x2,y2-r, x2,y2,   x2-r,y2, x1+r,y2,
            x1,y2,   x1,y2-r, x1,y1+r, x1,y1,
        ]
        self.create_polygon(pts, smooth=True, **kw)

    def rebuild(self, scale, currency=None, view_mode=None,
                border_hi=None, border_lo=None, line_color=None):
        self._scale = scale
        if currency is not None: self._currency = currency
        if view_mode is not None: self._view_mode = view_mode
        if border_hi: self._border_hi = border_hi
        if border_lo: self._border_lo = border_lo
        if line_color: self._line_color = line_color
        pw = max(30, int(BASE_LABEL_W * scale))
        ph = max(40, int(BASE_PANEL_H * scale))
        pad = max(3, int(4 * scale))
        self._pad, self._pw, self._ph = pad, pw, ph
        self.config(width=pw+pad*2, height=ph+pad*2)
        self._draw()

# ── Main Widget Window ─────────────────────────────────────────────────────────
class Tickoshi(tk.Tk):

    def __init__(self):
        super().__init__()
        self._cfg   = self._load_config()
        self._scale = self._cfg.get("scale", 1.0)
        self._currency = self._cfg.get("currency", "USD")
        self._topmost = self._cfg.get("topmost", True)
        self._refresh_s = self._cfg.get("refresh_s", 30)
        self._opacity = self._cfg.get("opacity", 0.97)
        self._view_mode = self._cfg.get("view_mode", "Price")
        self._border_color = self._cfg.get("border_color", "Gold")
        self._flash_enabled = self._cfg.get("flash", True)
        self._drag  = None
        self._last_price_str = None
        self._last_display_str = None
        self._prev_price = None        # for flash comparison
        self._num_digits = 5
        self._popup = None
        self._spark_pts = []           # perimeter points for spark overlay
        self._spark_overlay_ids = []   # temporary overlay line ids
        self._spark_running = False

        # Frameless, always-on-top
        self.overrideredirect(True)
        self.wm_attributes("-topmost", self._topmost)
        self.wm_attributes("-alpha", self._opacity)
        self.configure(bg=C_FACE)

        self._build_ui(self._num_digits)
        self._apply_position()

        # Bindings
        self.bind("<ButtonPress-1>",   self._ds)
        self.bind("<B1-Motion>",       self._dm)
        self.bind("<ButtonRelease-1>", self._de)
        self.bind("<Double-Button-1>", self._copy_to_clipboard)
        self.bind_all("<ButtonPress-3>", self._show_menu)
        self._bind_children()

        self._fetch_loop()

    # ── Border color helpers ──────────────────────────────────────────────────
    def _bc(self, key="hi"):
        return BORDER_COLORS.get(self._border_color, BORDER_COLORS["Gold"])[key]

    # ── Config ────────────────────────────────────────────────────────────────
    def _load_config(self) -> dict:
        try:
            with open(config_path()) as f:
                return json.load(f)
        except Exception:
            return {"x": 100, "y": 100, "scale": 1.0, "currency": "USD"}

    def _save_config(self):
        self._cfg["x"]            = self.winfo_x()
        self._cfg["y"]            = self.winfo_y()
        self._cfg["scale"]        = self._scale
        self._cfg["currency"]     = self._currency
        self._cfg["topmost"]      = self._topmost
        self._cfg["refresh_s"]    = self._refresh_s
        self._cfg["opacity"]      = self._opacity
        self._cfg["view_mode"]    = self._view_mode
        self._cfg["border_color"] = self._border_color
        self._cfg["flash"]        = self._flash_enabled
        try:
            with open(config_path(), "w") as f:
                json.dump(self._cfg, f, indent=2)
        except Exception:
            pass

    def _apply_position(self):
        x = self._cfg.get("x", 100)
        y = self._cfg.get("y", 100)
        self.geometry(f"+{x}+{y}")

    # ── UI Build ───────────────────────────────────────────────────────────────
    def _build_ui(self, num_digits):
        self._num_digits = num_digits
        self._spark_overlay_ids = []
        self._spark_running = False
        s = self._scale
        fp   = max(4, int(FACE_PAD * s))
        gap  = max(4, int(PANEL_GAP * s))

        for w in self.winfo_children():
            w.destroy()

        pad = max(3, int(4 * s))
        label_w = max(30, int(BASE_LABEL_W * s)) + pad * 2
        sign_w  = max(20, int(BASE_SIGN_W * s)) + pad * 2
        digit_w = max(30, int(BASE_PANEL_W * s)) + pad * 2
        panel_h = max(40, int(BASE_PANEL_H * s)) + pad * 2

        inner_w = label_w + gap + sign_w + gap + num_digits * digit_w + (num_digits - 1) * gap
        total_w = inner_w + fp * 2
        total_h = panel_h + fp * 2

        self._frame_cv = tk.Canvas(self, width=total_w, height=total_h,
                                   bg=C_FACE, highlightthickness=0)
        self._frame_cv.pack()

        # Smooth outer border polygon
        r = max(4, int(6 * s))
        border_w = max(2, int(2.5 * s))
        self._frame_border_w = border_w
        pts = _rr_pts(1, 1, total_w - 2, total_h - 2, r)
        self._frame_cv.create_polygon(pts, smooth=True, fill="",
                                      outline=self._bc("hi"), width=border_w)

        # Pre-compute perimeter points for spark chase overlay
        self._spark_pts = _perimeter_points(1, 1, total_w - 2, total_h - 2, r,
                                            SPARK_SEGMENTS)
        self._spark_overlay_ids = []

        # Panel row
        row = tk.Frame(self._frame_cv, bg=C_FACE)
        self._frame_cv.create_window(total_w // 2, total_h // 2,
                                     anchor="center", window=row)

        # Label panel
        self._label_panel = LabelPanel(
            row, scale=s, currency=self._currency, view_mode=self._view_mode,
            border_hi=self._bc("hi"), border_lo=self._bc("lo"),
            line_color=self._bc("line"))
        self._label_panel.pack(side="left", padx=(0, gap))

        # Sign panel
        sign = self._get_sign_symbol()
        self._sign_panel = SignPanel(
            row, scale=s, symbol=sign,
            border_hi=self._bc("hi"), border_lo=self._bc("lo"))
        self._sign_panel.pack(side="left", padx=(0, gap))

        # Digit panels
        self._digit_panels = []
        for i in range(num_digits):
            dp = DigitPanel(row, scale=s,
                            border_hi=self._bc("hi"), border_lo=self._bc("lo"))
            dp.pack(side="left", padx=(0, gap if i < num_digits - 1 else 0))
            self._digit_panels.append(dp)

        if self._last_display_str is not None:
            self._set_digits(self._last_display_str)

    def _get_sign_symbol(self) -> str:
        if self._view_mode == "Price":
            return CURRENCY_SIGNS.get(self._currency, "$")
        elif self._view_mode == "Block Height":
            return "#"
        elif self._view_mode == "Halving":
            return "\u23f3"
        return "$"

    # ── Spark chase animation ─────────────────────────────────────────────────
    def _spark_chase(self, color):
        """Run spark chase overlay around the border in the given color."""
        if not self._spark_pts or self._spark_running:
            return
        # Clean up any leftover overlay segments
        self._spark_cleanup()
        self._spark_running = True
        self._spark_step(color, 0)

    def _spark_cleanup(self):
        for sid in self._spark_overlay_ids:
            try:
                self._frame_cv.delete(sid)
            except Exception:
                pass
        self._spark_overlay_ids = []

    def _spark_step(self, color, step):
        if not self.winfo_exists() or not self._spark_pts:
            self._spark_cleanup()
            self._spark_running = False
            return
        n = SPARK_SEGMENTS

        if step >= n + SPARK_TRAIL:
            self._spark_cleanup()
            self._spark_running = False
            return

        # Remove previous overlay segments
        self._spark_cleanup()

        # Draw only the lit segments (trail) as overlay lines
        bw = self._frame_border_w
        for i in range(n):
            dist = step - i
            if 0 <= dist < SPARK_TRAIL:
                t = dist / SPARK_TRAIL
                c = _lerp(color, self._bc("hi"), t)
                x1, y1 = self._spark_pts[i]
                x2, y2 = self._spark_pts[(i + 1) % n]
                sid = self._frame_cv.create_line(
                    x1, y1, x2, y2, fill=c, width=bw + 1,
                    capstyle="round")
                self._spark_overlay_ids.append(sid)

        self.after(SPARK_MS, lambda: self._spark_step(color, step + 1))

    # ── Display update ────────────────────────────────────────────────────────
    def _update_display(self, display_str: str | None):
        if display_str is None:
            for dp in self._digit_panels:
                dp.set("-")
            return

        # Flash check (Price mode only)
        if self._flash_enabled and self._view_mode == "Price" and display_str is not None:
            try:
                new_val = int(display_str)
                if self._prev_price is not None:
                    if new_val > self._prev_price:
                        self._spark_chase("#00cc44")   # green = up
                    elif new_val < self._prev_price:
                        self._spark_chase("#cc2222")   # red = down
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

    def _set_digits(self, display_str: str):
        digits = list(display_str[-self._num_digits:])
        while len(digits) < self._num_digits:
            digits.insert(0, "")
        for i, dp in enumerate(self._digit_panels):
            dp.set(digits[i])

    # ── Fetch loop ─────────────────────────────────────────────────────────────
    def _fetch_loop(self):
        currency = self._currency
        view_mode = self._view_mode
        def _worker():
            _fetch_all_prices()
            _fetch_block_height()

            if view_mode == "Price":
                display = fetch_price(currency)
                self._last_price_str = display
            elif view_mode == "Block Height":
                display = fetch_block_height_str()
            elif view_mode == "Halving":
                with _price_cache_lock:
                    h = _block_height_cache.get("height")
                days = calc_halving_days(h)
                display = str(days) if days is not None else None
            else:
                display = fetch_price(currency)
                self._last_price_str = display

            if self.winfo_exists():
                self.after(0, lambda: self._update_display(display))
            if self.winfo_exists():
                self.after(self._refresh_s * 1000, self._fetch_loop)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

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
    def _ds(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _dm(self, e):
        if self._drag:
            self.geometry(f"+{e.x_root-self._drag[0]}+{e.y_root-self._drag[1]}")

    def _de(self, e):
        if self._drag:
            self._save_config()
        self._drag = None

    # ── Copy to clipboard ────────────────────────────────────────────────────
    def _copy_to_clipboard(self, e):
        if self._last_display_str:
            self.clipboard_clear()
            self.clipboard_append(self._last_display_str)

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

        # View submenu
        view_menu = tk.Menu(menu, **menu_style)
        for mode in VIEW_MODES:
            check = " \u2713" if mode == self._view_mode else ""
            view_menu.add_command(label=f"  {mode}{check}",
                                 command=lambda m=mode: self._menu_set_view(m))
        menu.add_cascade(label="  View", menu=view_menu)

        # Size submenu
        size_menu = tk.Menu(menu, **menu_style)
        for name, scale in SIZES.items():
            check = " \u2713" if abs(self._scale - scale) < 0.01 else ""
            size_menu.add_command(label=f"  {name}{check}",
                                 command=lambda s=scale: self._menu_set_size(s))
        menu.add_cascade(label="  Size", menu=size_menu)

        # Currency submenu
        curr_menu = tk.Menu(menu, **menu_style)
        for code, _, _ in CURRENCIES:
            check = " \u2713" if code == self._currency else ""
            curr_menu.add_command(label=f"  {code}{check}",
                                 command=lambda c=code: self._menu_set_currency(c))
        menu.add_cascade(label="  Currency", menu=curr_menu)

        # Refresh submenu
        ref_menu = tk.Menu(menu, **menu_style)
        for label, secs in REFRESH_OPTIONS:
            check = " \u2713" if self._refresh_s == secs else ""
            ref_menu.add_command(label=f"  {label}{check}",
                                command=lambda s=secs: self._menu_set_refresh(s))
        menu.add_cascade(label="  Refresh", menu=ref_menu)

        # Opacity submenu
        opa_menu = tk.Menu(menu, **menu_style)
        for label, val in OPACITY_OPTIONS:
            check = " \u2713" if abs(self._opacity - val) < 0.01 else ""
            opa_menu.add_command(label=f"  {label}{check}",
                                command=lambda v=val: self._menu_set_opacity(v))
        menu.add_cascade(label="  Opacity", menu=opa_menu)

        # Border color submenu
        brd_menu = tk.Menu(menu, **menu_style)
        for name in BORDER_COLORS:
            check = " \u2713" if name == self._border_color else ""
            brd_menu.add_command(label=f"  {name}{check}",
                                command=lambda n=name: self._menu_set_border(n))
        menu.add_cascade(label="  Border", menu=brd_menu)

        # Always on top toggle
        top_check = " \u2713" if self._topmost else ""
        menu.add_command(label=f"  Always on top{top_check}",
                         command=self._menu_toggle_topmost)

        # Price flash toggle
        flash_check = " \u2713" if self._flash_enabled else ""
        menu.add_command(label=f"  Price flash{flash_check}",
                         command=self._menu_toggle_flash)

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

    def _do_set_size(self, scale):
        self._scale = scale
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

    def _menu_set_refresh(self, secs):
        self._refresh_s = secs
        self._save_config()

    def _menu_set_opacity(self, val):
        self._opacity = val
        self.wm_attributes("-alpha", val)
        self._save_config()

    def _menu_set_view(self, mode):
        if mode == self._view_mode:
            return
        self.after(30, lambda: self._do_set_view(mode))

    def _do_set_view(self, mode):
        self._view_mode = mode
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

    def _menu_toggle_topmost(self):
        self._topmost = not self._topmost
        self.wm_attributes("-topmost", self._topmost)
        self._save_config()

    def _menu_toggle_flash(self):
        self._flash_enabled = not self._flash_enabled
        self._save_config()

    def _menu_quit(self):
        self.after(30, lambda: (self._save_config(), self.destroy()))

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = Tickoshi()
    app.mainloop()
