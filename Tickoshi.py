"""
BTC Widget — BlockClock Mini style desktop widget
Live Bitcoin price, frameless, always-on-top, draggable.
"""

import tkinter as tk
import threading
import json
import os
import urllib.request
import urllib.error

# ── Constants ────────────────────────────────────────────────────────────────
APP_NAME   = "BtcWidget"
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

# Panel base dimensions (at scale 1.0)
BASE_PANEL_W = 80
BASE_PANEL_H = 110
BASE_FS      = 62        # digit font size
BASE_R       = 10        # card corner radius

# Layout
FACE_PAD     = 6         # padding inside gold border
PANEL_GAP    = 8         # gap between panels

# Label panel
BASE_LABEL_W  = 78
BASE_LABEL_FS = 16

# ── Colors ───────────────────────────────────────────────────────────────────
C_FACE       = "#080808"   # face background
C_PANEL_BG   = "#0c0c0c"   # panel card background
C_BORDER_LO  = "#6a5820"   # panel border dim
C_BORDER_HI  = "#c9a84c"   # panel border gold highlight / outer frame
C_DIGIT      = "#ffffff"   # active digit
C_LABEL_TXT  = "#ffffff"   # "BTC/USD" label text
C_LABEL_LINE = "#c9a84c"   # divider line in label panel

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
    return os.path.join(d, "btc_config.json")

def _rr_pts(x1, y1, x2, y2, r):
    r = min(r, (x2-x1)//2, (y2-y1)//2)
    return [
        x1+r,y1, x2-r,y1, x2,y1,   x2,y1+r,
        x2,y2-r, x2,y2,   x2-r,y2, x1+r,y2,
        x1,y2,   x1,y2-r, x1,y1+r, x1,y1,
    ]

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
# Cache: fetch ALL currencies in one CoinGecko call to avoid rate limits
_price_cache = {}       # {"usd": 69500, "try": 2300000, ...}
_price_cache_lock = threading.Lock()

def _fetch_all_prices():
    """Fetch BTC price in all currencies with a single API call. Updates cache."""
    gecko_ids = ",".join(gid for _, _, gid in CURRENCIES)

    # Try CoinGecko first (single call for all currencies)
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=" + gecko_ids
        req = urllib.request.Request(url, headers={"User-Agent": "BtcWidget/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        with _price_cache_lock:
            for _, _, gid in CURRENCIES:
                if gid in data.get("bitcoin", {}):
                    _price_cache[gid] = float(data["bitcoin"][gid])
        return True
    except Exception:
        pass

    # Fallback: try Binance per-pair
    for code, bsym, gid in CURRENCIES:
        try:
            url = "https://api.binance.com/api/v3/ticker/price?symbol=" + bsym
            req = urllib.request.Request(url, headers={"User-Agent": "BtcWidget/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
            with _price_cache_lock:
                _price_cache[gid] = float(data["price"])
        except Exception:
            continue
    return bool(_price_cache)

def fetch_price(currency: str = "USD") -> str | None:
    """Returns price as integer string from cache, or None."""
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

# ── Digit Panel (FlipCard + gold border) ─────────────────────────────────────
class DigitPanel(tk.Canvas):
    """A canvas that draws the gold border frame and contains a FlipCard."""

    def __init__(self, parent, scale=1.0, **kw):
        self._scale = scale
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
                      outline=C_BORDER_LO, width=1)
        self._draw_rr(pad-2, pad-2, pw+pad+2, ph+pad+2, r+1,
                      outline=C_BORDER_HI, width=1)

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

    def rebuild(self, scale):
        self._scale = scale
        pw = max(30, int(BASE_PANEL_W * scale))
        ph = max(40, int(BASE_PANEL_H * scale))
        pad = max(3, int(4 * scale))
        self._pad, self._pw, self._ph = pad, pw, ph
        self.config(width=pw+pad*2, height=ph+pad*2)
        self.delete("all")
        self.card.rebuild(scale)
        self.create_window(pad, pad, anchor="nw", window=self.card)
        self._draw_border()

# ── Label Panel ───────────────────────────────────────────────────────────────
class LabelPanel(tk.Canvas):
    """Static panel showing BTC / <currency>."""

    def __init__(self, parent, scale=1.0, currency="USD", **kw):
        self._scale = scale
        self._currency = currency
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
        self._rr(1, 1, pw+pad*2-2, ph+pad*2-2, r+2, outline=C_BORDER_LO, width=1)
        self._rr(pad-2, pad-2, pw+pad+2, ph+pad+2, r+1, outline=C_BORDER_HI, width=1)

        # "BTC" top
        font_bold = ("Segoe UI", fs, "bold")
        self.create_text(cx, cy - int(ph * 0.15), text="BTC",
                         font=font_bold, fill=C_LABEL_TXT, anchor="center")
        # Divider line
        lw = int(pw * 0.6)
        self.create_line(cx - lw//2, cy + int(ph * 0.03),
                         cx + lw//2, cy + int(ph * 0.03),
                         fill=C_LABEL_LINE, width=max(1, int(1.5 * self._scale)))
        # Currency bottom
        self.create_text(cx, cy + int(ph * 0.22), text=self._currency,
                         font=font_bold, fill=C_LABEL_TXT, anchor="center")

    def _rr(self, x1, y1, x2, y2, r, **kw):
        r = min(r, (x2-x1)//2, (y2-y1)//2)
        pts = [
            x1+r,y1, x2-r,y1, x2,y1,   x2,y1+r,
            x2,y2-r, x2,y2,   x2-r,y2, x1+r,y2,
            x1,y2,   x1,y2-r, x1,y1+r, x1,y1,
        ]
        self.create_polygon(pts, smooth=True, **kw)

    def rebuild(self, scale, currency=None):
        self._scale = scale
        if currency is not None:
            self._currency = currency
        pw = max(30, int(BASE_LABEL_W * scale))
        ph = max(40, int(BASE_PANEL_H * scale))
        pad = max(3, int(4 * scale))
        self._pad, self._pw, self._ph = pad, pw, ph
        self.config(width=pw+pad*2, height=ph+pad*2)
        self._draw()

# ── Main Widget Window ─────────────────────────────────────────────────────────
class BtcWidget(tk.Tk):

    def __init__(self):
        super().__init__()
        self._cfg   = self._load_config()
        self._scale = self._cfg.get("scale", 1.0)
        self._currency = self._cfg.get("currency", "USD")
        self._topmost = self._cfg.get("topmost", True)
        self._refresh_s = self._cfg.get("refresh_s", 30)
        self._drag  = None
        self._last_price_str = None
        self._num_digits = 5       # initial guess; rebuilt on first price
        self._popup = None         # current popup menu reference

        # Frameless, always-on-top
        self.overrideredirect(True)
        self.wm_attributes("-topmost", self._topmost)
        self.wm_attributes("-alpha", 0.97)
        self.configure(bg=C_FACE)

        self._build_ui(self._num_digits)
        self._apply_position()

        # Bind drag on root ONCE; children get drag bindings after each rebuild
        self.bind("<ButtonPress-1>",   self._ds)
        self.bind("<B1-Motion>",       self._dm)
        self.bind("<ButtonRelease-1>", self._de)
        # Right-click: bind_all so it fires exactly once regardless of widget
        self.bind_all("<ButtonPress-3>", self._show_menu)
        self._bind_children()

        # Start background price fetcher
        self._fetch_loop()

    # ── Config ────────────────────────────────────────────────────────────────
    def _load_config(self) -> dict:
        try:
            with open(config_path()) as f:
                return json.load(f)
        except Exception:
            return {"x": 100, "y": 100, "scale": 1.0, "currency": "USD"}

    def _save_config(self):
        self._cfg["x"]         = self.winfo_x()
        self._cfg["y"]         = self.winfo_y()
        self._cfg["scale"]     = self._scale
        self._cfg["currency"]  = self._currency
        self._cfg["topmost"]   = self._topmost
        self._cfg["refresh_s"] = self._refresh_s
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
        s = self._scale
        fp   = max(4, int(FACE_PAD * s))
        gap  = max(4, int(PANEL_GAP * s))

        # Destroy old widgets (but not popup menus)
        for w in self.winfo_children():
            w.destroy()

        # Calculate sizes
        pad = max(3, int(4 * s))
        label_w = max(30, int(BASE_LABEL_W * s)) + pad * 2
        digit_w = max(30, int(BASE_PANEL_W * s)) + pad * 2
        panel_h = max(40, int(BASE_PANEL_H * s)) + pad * 2

        inner_w = label_w + gap + num_digits * digit_w + (num_digits - 1) * gap
        total_w = inner_w + fp * 2
        total_h = panel_h + fp * 2

        # Outer frame canvas (gold border on black face)
        self._frame_cv = tk.Canvas(self, width=total_w, height=total_h,
                                   bg=C_FACE, highlightthickness=0)
        self._frame_cv.pack()

        # Gold border line
        r = max(4, int(6 * s))
        border_w = max(2, int(2.5 * s))
        pts = _rr_pts(1, 1, total_w - 2, total_h - 2, r)
        self._frame_cv.create_polygon(pts, smooth=True, fill="", outline=C_BORDER_HI,
                                      width=border_w)

        # Panel row
        row = tk.Frame(self._frame_cv, bg=C_FACE)
        self._frame_cv.create_window(
            total_w // 2, total_h // 2,
            anchor="center", window=row
        )

        # Label panel
        self._label_panel = LabelPanel(row, scale=s, currency=self._currency)
        self._label_panel.pack(side="left", padx=(0, gap))

        # Digit panels
        self._digit_panels = []
        for i in range(num_digits):
            dp = DigitPanel(row, scale=s)
            dp.pack(side="left", padx=(0, gap if i < num_digits - 1 else 0))
            self._digit_panels.append(dp)

        # Re-apply last known price to new panels
        if self._last_price_str is not None:
            self._set_digits(self._last_price_str)

    # ── Price display ──────────────────────────────────────────────────────────
    def _update_price(self, price_str: str | None):
        """Map price string to digit panels. Rebuild if digit count changed."""
        if price_str is None:
            for dp in self._digit_panels:
                dp.set("-")
            return

        self._last_price_str = price_str
        needed = min(max(len(price_str), 1), MAX_DIGITS)

        # Accordion: rebuild panels if digit count changed
        if needed != self._num_digits:
            pos_x, pos_y = self.winfo_x(), self.winfo_y()
            self._build_ui(needed)
            self._bind_children()
            self.geometry(f"+{pos_x}+{pos_y}")
        else:
            self._set_digits(price_str)

    def _set_digits(self, price_str: str):
        digits = list(price_str[-self._num_digits:])
        while len(digits) < self._num_digits:
            digits.insert(0, "")
        for i, dp in enumerate(self._digit_panels):
            dp.set(digits[i])

    # ── Fetch loop ─────────────────────────────────────────────────────────────
    def _fetch_loop(self):
        currency = self._currency
        def _worker():
            _fetch_all_prices()   # batch-fetch all currencies in one call
            price = fetch_price(currency)
            if self.winfo_exists():
                self.after(0, lambda: self._update_price(price))
            if self.winfo_exists():
                self.after(self._refresh_s * 1000, self._fetch_loop)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    # ── Bind children (called after each rebuild) ──────────────────────────────
    def _bind_children(self):
        """Bind drag + right-click on all child widgets (fresh after rebuild)."""
        for c in self.winfo_children():
            self._bind_recursive(c)

    def _bind_recursive(self, w):
        w.bind("<ButtonPress-1>",   self._ds)
        w.bind("<B1-Motion>",       self._dm)
        w.bind("<ButtonRelease-1>", self._de)
        for c in w.winfo_children():
            self._bind_recursive(c)

    # ── Drag to move ───────────────────────────────────────────────────────────
    def _ds(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _dm(self, e):
        if self._drag:
            self.geometry(f"+{e.x_root-self._drag[0]}+{e.y_root-self._drag[1]}")

    def _de(self, e):
        if self._drag:
            self._save_config()
        self._drag = None

    # ── Right-click menu ──────────────────────────────────────────────────────
    def _show_menu(self, e):
        # Dismiss any existing popup first
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

        # Refresh interval submenu
        ref_menu = tk.Menu(menu, **menu_style)
        for label, secs in REFRESH_OPTIONS:
            check = " \u2713" if self._refresh_s == secs else ""
            ref_menu.add_command(label=f"  {label}{check}",
                                command=lambda s=secs: self._menu_set_refresh(s))
        menu.add_cascade(label="  Refresh", menu=ref_menu)

        # Always on top toggle
        top_check = " \u2713" if self._topmost else ""
        menu.add_command(label=f"  Always on top{top_check}",
                         command=self._menu_toggle_topmost)

        menu.add_separator()
        menu.add_command(label="  Close", command=self._menu_quit)

        menu.tk_popup(e.x_root, e.y_root)
        # Clean up when menu is dismissed (item click, Escape, or click-away)
        menu.bind("<Unmap>", self._on_popup_close)
        return "break"

    def _on_popup_close(self, _event=None):
        """Release any stale grab left by tk_popup and clean up the menu."""
        self._popup = None
        try:
            self.grab_release()
        except Exception:
            pass

    def _menu_set_size(self, scale):
        # Let native popup close itself, then rebuild after a short delay
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

    def _menu_toggle_topmost(self):
        self._topmost = not self._topmost
        self.wm_attributes("-topmost", self._topmost)
        self._save_config()

    def _menu_quit(self):
        self.after(30, lambda: (self._save_config(), self.destroy()))

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = BtcWidget()
    app.mainloop()
