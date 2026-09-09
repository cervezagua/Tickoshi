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
import websocket

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
if sys.platform == "darwin":
    _FONT_FAMILY = "Helvetica Neue"
elif os.name == "nt":
    _FONT_FAMILY = "Segoe UI"
else:
    _FONT_FAMILY = "DejaVu Sans"

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
CURRENCY_TO_GECKO = {code: gid for code, _, gid in CURRENCIES}

# Currency symbols
CURRENCY_SIGNS = {
    "USD": "$", "TRY": "\u20ba", "EUR": "\u20ac",
    "GBP": "\u00a3", "JPY": "\u00a5", "RUB": "\u20bd",
}

# Secondary-row modules (key, menu label). Primary row always shows Price.
MODULES = [
    ("fees",    "Fees"),
    ("sats",    "Sats"),
    ("height",  "Block Height"),
    ("halving", "Halving"),
    ("hash",    "Hashrate"),
    ("mempool", "Mempool"),
]
MODULE_KEYS = {k for k, _ in MODULES}

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

# Border pulse animation (price up/down flash)
PULSE_MS         = 30     # ms per animation step
PULSE_STEPS      = 100    # total steps (30*100 = 3000ms fade)
PULSE_WIDTH_MULT = 3.0    # peak border thickness multiplier

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
    elif sys.platform == "darwin":             # macOS
        base = os.path.join(os.path.expanduser("~"),
                            "Library", "Application Support")
    else:                                      # Linux
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
        FONT = (_FONT_FAMILY, self.FS, "bold")

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
_fees_cache = {"low": None, "med": None, "high": None}
# "hashrate_ts" marks when a hashrate last arrived, so the historical figure in
# the WS init payload doesn't overwrite a current one.
_network_cache = {"hashrate_ehs": None, "hashrate_ts": None, "mempool_mb": None}
_price_cache_lock = threading.Lock()

# Fees and mempool size arrive only over the WebSocket, so when that feed goes
# quiet there is nothing to refresh them and the last reading would sit on
# screen looking live. Freshness is judged on the connection rather than on the
# individual field: mempool.space decides how often it repeats any given value,
# but a socket that has pushed nothing at all for this long is dead.
STALE_AFTER_S = 600
_ws_alive_ts = None   # time of the last message accepted from the WebSocket

def _is_fresh(ts) -> bool:
    return isinstance(ts, (int, float)) and (time.time() - ts) < STALE_AFTER_S

def _ws_feed_is_live() -> bool:
    return _is_fresh(_ws_alive_ts)

def _http_get(url, timeout=8, what=""):
    """GET `url` and return the decoded body, or None on failure.

    Failures are logged with the detail that actually tells the modes apart:
    an HTTP status plus the server's own error body (rate limit, invalid
    symbol) versus a transport error (DNS, TLS, timeout, no route).
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Tickoshi/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="replace").strip()[:160]
        except Exception:
            pass
        _debug_log(f"http {what} failed: HTTP {e.code} {e.reason}"
                   + (f"  body={body}" if body else ""))
    except Exception as e:
        _debug_log(f"http {what} failed: {type(e).__name__}: {e}")
    return None

def _fetch_all_prices(preferred: str | None = None):
    """Refresh the price cache: CoinGecko first (one request covers every
    currency), Binance per-symbol as the fallback.

    Returns True if *this call* got a live quote for `preferred`. The cache
    keeps the last good price, so asking whether it is non-empty would report
    success indefinitely after the network dropped.
    """
    gecko_ids = ",".join(gid for _, _, gid in CURRENCIES)
    pref_gid = CURRENCY_TO_GECKO.get(preferred)
    stored = set()

    body = _http_get(
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies="
        + gecko_ids, timeout=8, what="price (coingecko)")
    if body is not None:
        try:
            quotes = json.loads(body).get("bitcoin") or {}
        except Exception as e:
            quotes = {}
            _debug_log(f"http price (coingecko) bad body: {type(e).__name__}: {e}")
        with _price_cache_lock:
            for _, _, gid in CURRENCIES:
                v = quotes.get(gid)
                if isinstance(v, (int, float)) and v > 0:
                    _price_cache[gid] = float(v)
                    stored.add(gid)
        if not stored:
            # CoinGecko answers 200 with an empty or error-shaped body when it
            # is rate limiting or an id is unknown. Treating the request itself
            # as success there skipped the fallback and left the price blank
            # until restart, so a usable quote is what counts as success.
            _debug_log(f"http price (coingecko) no usable quotes: {body[:160]}")

    def _got_wanted():
        # A response that carried every currency but the one on screen still
        # leaves the display blank, so success keys off the wanted quote.
        return (pref_gid in stored) if pref_gid else bool(stored)

    if not _got_wanted():
        # Only the selected currency is ever displayed and switching currency
        # kicks off a fresh fetch, so stop at the first working symbol rather
        # than walking all six at up to 5s each while the network is down.
        order = [c for c in CURRENCIES if c[0] == preferred]
        order += [c for c in CURRENCIES if c not in order]
        for code, bsym, gid in order:
            body = _http_get(
                "https://api.binance.com/api/v3/ticker/price?symbol=" + bsym,
                timeout=5, what=f"price (binance {bsym})")
            if body is None:
                continue
            try:
                price = float(json.loads(body)["price"])
            except Exception as e:
                _debug_log(f"http price (binance {bsym}) bad body: "
                           f"{type(e).__name__}: {e}")
                continue
            if price > 0:
                with _price_cache_lock:
                    _price_cache[gid] = price
                stored.add(gid)
                if preferred is None or code == preferred:
                    break

    return _got_wanted()

def _fetch_hashrate():
    """Populate _network_cache['hashrate_ehs'] from mempool.space's free
    mining endpoint. Endpoint returns {"currentHashrate": <H/s>, ...}."""
    body = _http_get("https://mempool.space/api/v1/mining/hashrate/3d",
                     timeout=8, what="hashrate")
    if body is None:
        return None
    try:
        hr = json.loads(body).get("currentHashrate")
    except Exception as e:
        _debug_log(f"http hashrate bad body: {type(e).__name__}: {e}")
        return None
    if isinstance(hr, (int, float)) and hr > 0:
        with _price_cache_lock:
            _network_cache["hashrate_ehs"] = hr / 1e18
            _network_cache["hashrate_ts"] = time.time()
            ehs = _network_cache["hashrate_ehs"]
        _debug_log(f"http hashrate  H/s={hr} -> {ehs:.2f} EH/s")
        return ehs
    _debug_log(f"http hashrate no usable currentHashrate: {body[:120]}")
    return None

def _fetch_block_height():
    body = _http_get("https://blockchain.info/q/getblockcount", timeout=8,
                     what="block height")
    if body is not None:
        try:
            height = int(body.strip())
        except ValueError:
            # Previously swallowed with no log at all, which made a failing
            # height the one connectivity fault invisible in the debug log.
            _debug_log(f"http block height unparseable: {body[:80]!r}")
        else:
            with _price_cache_lock:
                _block_height_cache["height"] = height
            return height
    with _price_cache_lock:
        return _block_height_cache.get("height")

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

def _log_startup_env():
    """First line of every session's log: the facts a connectivity report
    otherwise has to guess at — interpreter, platform, client version, whether
    a proxy is in play, and whether there is a CA store to verify TLS against
    (an empty one is what makes every HTTPS/WSS call fail to verify)."""
    try:
        ws_ver = getattr(websocket, "__version__", "?")
    except Exception:
        ws_ver = "?"
    try:
        import ssl
        ca_count = ssl.create_default_context().cert_store_stats()["x509_ca"]
    except Exception as e:
        ca_count = f"unavailable ({type(e).__name__})"
    # Names only — proxy URLs routinely embed credentials.
    proxy_vars = sorted(k for k in os.environ
                        if k.lower() in ("http_proxy", "https_proxy",
                                         "all_proxy", "no_proxy"))
    _debug_log(f"--- {APP_NAME} start  python={sys.version.split()[0]} "
               f"platform={sys.platform}  websocket-client={ws_ver}  "
               f"ca_certs={ca_count}  proxy_env={proxy_vars or 'none'}")

# ── Mempool.space WebSocket: source of truth for Priority tile values ─────────
# Public, free endpoint. Pushes live `fees` object with fractional sat/vB values
# that /fees/recommended REST clamps to integer >= 1.
_ws_thread = None
_ws_stop_flag = threading.Event()
_ws_app = None  # active WebSocketApp instance (for .close())
_ws_keys_logged = False  # one-shot top-level-key dump per connection
_ws_lock = threading.Lock()
_ws_gen = 0        # identity of the run allowed to own the socket
_ws_running = False  # a run at the current generation is serving
_ws_logged = {}    # last logged value per feed, to keep the log from churning

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
    global _ws_keys_logged, _ws_alive_ts
    try:
        msg = json.loads(raw)
    except Exception:
        return
    if not isinstance(msg, dict):
        return
    _ws_alive_ts = time.time()

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
        # rolled connection errors out of the 200-line window within minutes,
        # so only a change earns a line.
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

    # Hashrate — live field from difficulty-adjustment block: {"da": {"currentHashrate": <H/s>, ...}}
    da = msg.get("da")
    if isinstance(da, dict):
        hr = da.get("currentHashrate")
        if isinstance(hr, (int, float)) and hr > 0:
            with _price_cache_lock:
                _network_cache["hashrate_ehs"] = hr / 1e18
                _network_cache["hashrate_ts"] = time.time()
                ehs = _network_cache["hashrate_ehs"]
            if _ws_logged.get("hashrate") != round(ehs, 2):
                _ws_logged["hashrate"] = round(ehs, 2)
                _debug_log(f"ws hashrate  H/s={hr} -> {ehs:.2f} EH/s")

    # Hashrate fallback — init payload carries a "hashrates" array of historical points.
    hrs = msg.get("hashrates")
    if isinstance(hrs, list) and hrs:
        last = hrs[-1]
        if isinstance(last, dict):
            hr = last.get("avgHashrate")
            if isinstance(hr, (int, float)) and hr > 0:
                with _price_cache_lock:
                    # Don't overwrite a fresher "da.currentHashrate" value.
                    if not _is_fresh(_network_cache["hashrate_ts"]):
                        _network_cache["hashrate_ehs"] = hr / 1e18
                        _network_cache["hashrate_ts"] = time.time()

def _ws_on_error(ws, err):
    _debug_log(f"ws ERROR: {type(err).__name__}: {err}")

def _ws_on_close(ws, code, reason):
    _debug_log(f"ws CLOSED code={code!r} reason={reason!r}")

def _ws_run(gen):
    """Connect-and-reconnect loop for one generation of the feed.

    `gen` is this run's claim on the socket: _ws_start/_ws_stop bump the
    global generation, so a run left over from an earlier stop retires at its
    next check instead of lingering or fighting over _ws_app.
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
        # waiting the full 60s for the rest of the session.
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
        # Retire any run still winding down from a previous _ws_stop(). The
        # old is_alive() check alone let a stop-then-start (toggling a WS tile
        # off and straight back on) return early against a thread that was
        # about to exit, leaving the feed dead until the app restarted.
        _ws_gen += 1
        gen = _ws_gen
        _ws_running = True
        _ws_thread = threading.Thread(target=_ws_run, args=(gen,), daemon=True)
        _ws_thread.start()
    _debug_log("ws start requested")

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
    _debug_log("ws stop requested")

def fetch_price(currency: str = "USD") -> str | None:
    gecko_id = CURRENCY_TO_GECKO.get(currency, "usd")
    with _price_cache_lock:
        price = _price_cache.get(gecko_id)
    if price is not None:
        return str(int(round(price)))
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
        fs = max(12, int(BASE_FS * self._scale))

        # Card background
        self._draw_rr(pad, pad, pad+pw, pad+ph, r, fill=C_PANEL_BG, outline="")

        # Border — fill="" is required: Tk's default polygon fill is a dynamic
        # system color on macOS Aqua (white in dark mode), which would paint
        # over the card and hide the text.
        self._draw_rr(1, 1, pw+pad*2-2, ph+pad*2-2, r+2,
                      fill="", outline=self._border_lo, width=1)
        self._draw_rr(pad-2, pad-2, pw+pad+2, ph+pad+2, r+1,
                      fill="", outline=self._border_hi, width=1)

        # Symbol text
        cx = pad + pw // 2
        cy = pad + ph // 2
        font = (_FONT_FAMILY, fs, "bold")
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

    def __init__(self, parent, scale=1.0, currency="USD",
                 border_hi="#c9a84c", border_lo="#6a5820", line_color="#c9a84c", **kw):
        self._scale = scale
        self._currency = currency
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

        # Border — fill="" is required: Tk's default polygon fill is a dynamic
        # system color on macOS Aqua (white in dark mode), which would paint
        # over the card and hide the text.
        self._rr(1, 1, pw+pad*2-2, ph+pad*2-2, r+2, fill="", outline=self._border_lo, width=1)
        self._rr(pad-2, pad-2, pw+pad+2, ph+pad+2, r+1, fill="", outline=self._border_hi, width=1)

        font_bold = (_FONT_FAMILY, fs, "bold")

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

    def rebuild(self, scale, currency=None,
                border_hi=None, border_lo=None, line_color=None):
        self._scale = scale
        if currency is not None: self._currency = currency
        if border_hi: self._border_hi = border_hi
        if border_lo: self._border_lo = border_lo
        if line_color: self._line_color = line_color
        pw = max(30, int(BASE_LABEL_W * scale))
        ph = max(40, int(BASE_PANEL_H * scale))
        pad = max(3, int(4 * scale))
        self._pad, self._pw, self._ph = pad, pw, ph
        self.config(width=pw+pad*2, height=ph+pad*2)
        self._draw()

# ── Fee Block (short wide Low/Med/High priority panel) ───────────────────────
class FeeBlock(tk.Canvas):

    def __init__(self, parent, total_w, scale=1.0, label="LOW",
                 unit="sat/vB",
                 border_hi="#c9a84c", border_lo="#6a5820",
                 line_color="#c9a84c", **kw):
        self._scale = scale
        self._label = label
        self._unit = unit
        self._value = None
        self._border_hi = border_hi
        self._border_lo = border_lo
        self._line_color = line_color
        pad = max(3, int(4 * scale))
        pw = max(60, total_w - pad * 2)
        ph = max(24, int(BASE_FEE_H * scale))
        super().__init__(parent, width=pw + pad*2, height=ph + pad*2,
                         bg=C_FACE, highlightthickness=0, **kw)
        self._pad, self._pw, self._ph = pad, pw, ph
        self._draw()

    def _draw(self):
        self.delete("all")
        pad = self._pad
        pw, ph = self._pw, self._ph
        r   = max(4, int(BASE_R * self._scale * 0.8))
        lfs = max(10, int(BASE_LABEL_FS * self._scale * 1.0))   # LOW/MED/HIGH
        vfs = max(14, int(BASE_FS * self._scale * 0.40))        # value
        ufs = max(8,  int(BASE_LABEL_FS * self._scale * 0.65))  # sat/vB
        cy  = pad + ph // 2

        # Card background
        self._rr(pad, pad, pad+pw, pad+ph, r, fill=C_PANEL_BG, outline="")

        # Border — fill="" is required: Tk's default polygon fill is a dynamic
        # system color on macOS Aqua (white in dark mode), which would paint
        # over the card and hide the text.
        self._rr(1, 1, pw+pad*2-2, ph+pad*2-2, r+2,
                 fill="", outline=self._border_lo, width=1)
        self._rr(pad-2, pad-2, pw+pad+2, ph+pad+2, r+1,
                 fill="", outline=self._border_hi, width=1)

        # Horizontal layout: [ LABEL | VALUE  unit ]
        left_cx   = pad + int(pw * 0.19)
        div_x     = pad + int(pw * 0.38)
        inner_gap = max(6, int(8 * self._scale))
        edge_pad  = max(4, int(6 * self._scale))

        # Label (left)
        self.create_text(left_cx, cy, text=self._label,
                         font=(_FONT_FAMILY, lfs, "bold"),
                         fill=C_LABEL_TXT, anchor="center")

        # Vertical divider
        div_h = int(ph * 0.55)
        self.create_line(div_x, cy - div_h//2, div_x, cy + div_h//2,
                         fill=self._line_color,
                         width=max(1, int(1.5 * self._scale)))

        # Value — anchored west, sitting a fixed gap right of the divider.
        val_text = str(self._value) if self._value is not None else "--"
        self.create_text(div_x + inner_gap, cy, text=val_text,
                         font=(_FONT_FAMILY, vfs, "bold"),
                         fill=C_DIGIT, anchor="w")

        # Unit — anchored east, hugging the right edge.
        self.create_text(pad + pw - edge_pad, cy, text=self._unit,
                         font=(_FONT_FAMILY, ufs, "bold"),
                         fill=C_LABEL_TXT, anchor="e")

    def _rr(self, x1, y1, x2, y2, r, **kw):
        r = min(r, (x2-x1)//2, (y2-y1)//2)
        pts = [
            x1+r,y1, x2-r,y1, x2,y1,   x2,y1+r,
            x2,y2-r, x2,y2,   x2-r,y2, x1+r,y2,
            x1,y2,   x1,y2-r, x1,y1+r, x1,y1,
        ]
        self.create_polygon(pts, smooth=True, **kw)

    def set_value(self, n):
        if n == self._value:
            return
        self._value = n
        self._draw()

    def rebuild(self, total_w, scale, border_hi=None, border_lo=None, line_color=None):
        self._scale = scale
        if border_hi:  self._border_hi = border_hi
        if border_lo:  self._border_lo = border_lo
        if line_color: self._line_color = line_color
        pad = max(3, int(4 * scale))
        pw = max(60, total_w - pad * 2)
        ph = max(24, int(BASE_FEE_H * scale))
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
        self._flash_enabled = self._cfg.get("flash", True)
        self._locked = self._cfg.get("locked", False)
        self._drag  = None
        self._last_price_str = None
        self._last_display_str = None
        self._prev_price = None        # for flash comparison
        self._num_digits = 5
        self._popup = None
        self._frame_border_id = None   # canvas id of the outer border polygon
        self._pulse_after_id = None    # in-flight pulse animation timer
        self._result_queue = queue.Queue()
        self._fetch_after_id = None    # pending fetch-loop timer
        self._fetch_gen = 0            # latest fetch generation; stale workers no-op

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
        self.configure(bg=C_FACE)

        self._build_ui(self._num_digits)
        self._apply_position()

        # Bindings
        self.bind("<ButtonPress-1>",   self._ds)
        self.bind("<B1-Motion>",       self._dm)
        self.bind("<ButtonRelease-1>", self._de)
        self.bind("<Double-Button-1>", self._copy_to_clipboard)
        self.bind_all("<ButtonPress-3>", self._show_menu)
        if sys.platform == "darwin":
            # macOS: Button-2 fires from some trackpad secondary-click configs;
            # Ctrl-click is the canonical Mac fallback. _show_menu doesn't
            # inspect which button fired, so aliasing both is safe.
            self.bind_all("<ButtonPress-2>", self._show_menu)
            self.bind_all("<Control-Button-1>", self._show_menu)
        self._bind_children()

        _log_startup_env()
        self._start_result_poller()
        self._fetch_loop()

        # Start mempool.space WS if any WS-fed module is enabled at startup.
        if self._needs_ws():
            _ws_start()

    # ── Border color helpers ──────────────────────────────────────────────────
    def _bc(self, key="hi"):
        return BORDER_COLORS.get(self._border_color, BORDER_COLORS["Gold"])[key]

    def _needs_ws(self) -> bool:
        # Modules that consume data pushed over the mempool.space WebSocket.
        return any(m in self._modules for m in ("fees", "hash", "mempool"))

    # ── Config ────────────────────────────────────────────────────────────────
    def _load_config(self) -> dict:
        path = config_path()
        if not os.path.exists(path):
            return {"x": 100, "y": 100, "scale": 1.0, "currency": "USD"}
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"[Tickoshi] Failed to load config {path}: {e}",
                  file=sys.stderr)
            return {"x": 100, "y": 100, "scale": 1.0, "currency": "USD"}
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
        self._cfg["flash"]        = self._flash_enabled
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

        self._num_digits = num_digits
        s = self._scale
        fp   = max(4, int(FACE_PAD * s))
        gap  = max(4, int(PANEL_GAP * s))
        # Secondary rows: walk self._modules in selection order. 'fees' takes
        # its own full-width row. Other tiles pair into half-width rows (two
        # per row) to keep the widget short. An odd trailing tile renders
        # full-width so it doesn't look stranded.
        singles = [k for k in self._modules if k != "fees"]
        n_single_rows = (len(singles) + 1) // 2
        n_fee_rows = 1 if "fees" in self._modules else 0
        sec_rows = n_single_rows + n_fee_rows

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

        # Smooth outer border polygon
        r = max(4, int(6 * s))
        border_w = max(2, int(2.5 * s))
        self._frame_border_w = border_w
        pts = _rr_pts(1, 1, total_w - 2, total_h - 2, r)
        self._frame_border_id = self._frame_cv.create_polygon(
            pts, smooth=True, fill="",
            outline=self._bc("hi"), width=border_w)

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

        # Secondary-row panel refs — reset each rebuild.
        self._fee_panels = []
        self._sats_panel = None
        self._height_panel = None
        self._halving_panel = None
        self._hash_panel = None
        self._mempool_panel = None

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
                              line_color=self._bc("line"))
                fb.pack(side="left", padx=(0, gap if i < 2 else 0))
                self._fee_panels.append(fb)
            with _price_cache_lock:
                vals = (_fees_cache.get("low"),
                        _fees_cache.get("med"),
                        _fees_cache.get("high"))
            for panel, v in zip(self._fee_panels, vals):
                panel.set_value(v)

        def _build_single_tile(parent, key, width, right_pad):
            sign = CURRENCY_SIGNS.get(self._currency, "$")
            spec = {
                "sats":    ("SATS",   f"per {sign}"),
                "height":  ("HEIGHT", "blk"),
                "halving": ("HALVING", "days"),
                "hash":    ("HASH",   "EH/s"),
                "mempool": ("MEMP",   "MB"),
            }[key]
            fb = FeeBlock(parent, total_w=width, scale=s,
                          label=spec[0], unit=spec[1],
                          border_hi=self._bc("hi"), border_lo=self._bc("lo"),
                          line_color=self._bc("line"))
            fb.pack(side="left", padx=(0, right_pad))
            attr = {"sats": "_sats_panel", "height": "_height_panel",
                    "halving": "_halving_panel", "hash": "_hash_panel",
                    "mempool": "_mempool_panel"}[key]
            setattr(self, attr, fb)

        # Walk modules in selection order. Pair adjacent non-fees tiles into
        # half-width rows; 'fees' flushes any pending pair and takes a full row.
        pair_buf = []

        def flush_pairs():
            if not pair_buf:
                return
            row = _new_row()
            if len(pair_buf) == 1:
                _build_single_tile(row, pair_buf[0], inner_w, 0)
            else:
                half_w = (inner_w - gap) // 2
                _build_single_tile(row, pair_buf[0], half_w, gap)
                _build_single_tile(row, pair_buf[1], half_w, 0)
            pair_buf.clear()

        for key in self._modules:
            if key == "fees":
                flush_pairs()
                _build_fees_row()
            else:
                pair_buf.append(key)
                if len(pair_buf) == 2:
                    flush_pairs()
        flush_pairs()

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
        # Unlike fees/mempool this has an HTTP fallback polled on the user's
        # own refresh interval, so it is not gated on the WebSocket being live.
        with _price_cache_lock:
            ehs = _network_cache.get("hashrate_ehs")
        if not isinstance(ehs, (int, float)) or ehs <= 0:
            return "--"
        return f"{ehs:,.0f}" if ehs >= 100 else f"{ehs:,.1f}"

    def _compute_mempool_str(self) -> str:
        with _price_cache_lock:
            mb = _network_cache.get("mempool_mb")
        if not _ws_feed_is_live() or not isinstance(mb, (int, float)) or mb < 0:
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

    # ── Border pulse animation ────────────────────────────────────────────────
    def _pulse(self, color):
        """Flash the entire outer border in `color`, fading back to gold."""
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

    def _pulse_step(self, color, step):
        if not self.winfo_exists() or self._frame_border_id is None:
            self._pulse_after_id = None
            return

        base  = self._bc("hi")
        total = PULSE_STEPS

        if step >= total:
            # Restore the resting border exactly.
            try:
                self._frame_cv.itemconfig(self._frame_border_id,
                                          outline=base,
                                          width=self._frame_border_w)
            except Exception:
                pass
            self._pulse_after_id = None
            return

        # Eased fade from flash color back to gold.
        t = _smoothstep(step / total)
        current = _lerp(color, base, t)
        thick   = max(
            self._frame_border_w,
            int(self._frame_border_w * (1 + (PULSE_WIDTH_MULT - 1) * (1 - t))))

        try:
            self._frame_cv.itemconfig(self._frame_border_id,
                                      outline=current,
                                      width=thick)
        except Exception:
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
            # Fees only arrive over the WebSocket. If that feed has gone quiet,
            # blank the tile rather than leave a stale estimate looking live.
            with _price_cache_lock:
                if _ws_feed_is_live():
                    vals = (_fees_cache.get("low"),
                            _fees_cache.get("med"),
                            _fees_cache.get("high"))
                else:
                    vals = (None, None, None)
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
            display = None
            try:
                refreshed = _fetch_all_prices(currency)
                if needs_height:
                    _fetch_block_height()
                if "hash" in modules:
                    _fetch_hashrate()
                # Fees + mempool are pushed via WebSocket — no HTTP fetch here.
                display = fetch_price(currency)
                if not refreshed:
                    _debug_log(f"fetch cycle: no live {currency} price from any "
                               f"source (showing "
                               f"{'last known' if display else 'nothing'})")
            except Exception as e:
                _debug_log(f"fetch worker crashed: {type(e).__name__}: {e}")
            finally:
                self._result_queue.put(
                    lambda: self._on_fetch_done(display, currency, modules, gen))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_fetch_done(self, display, currency, modules, gen):
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
                    self._refresh_s * 1000, self._fetch_loop)

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
        if self._locked:
            return
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _dm(self, e):
        if self._locked or not self._drag:
            return
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
        for name, scale in SIZES.items():
            check = " \u2713" if abs(self._scale - scale) < 0.01 else ""
            size_menu.add_command(label=f"  {name}{check}",
                                 command=lambda s=scale: self._menu_set_size(s))
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

        lock_check = " \u2713" if self._locked else ""
        menu.add_command(label=f"  Lock{lock_check}",
                         command=self._menu_toggle_lock)

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
