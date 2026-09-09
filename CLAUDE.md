# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Run from source: `pip install websocket-client && python Tickoshi.py` (Python 3.10+; Linux also needs `python3-tk`)
- Build Windows EXE: `BUILD.bat` → `dist\Tickoshi.exe` (PyInstaller `--onefile --windowed`; auto-installs pyinstaller, pillow, websocket-client; kills a running Tickoshi.exe first)
- Build Linux binary: `./BUILD.sh` → `dist/Tickoshi` plus a `~/.local/share/applications/tickoshi.desktop` launcher
- Build macOS .app: `./BUILD.command` → `dist/Tickoshi.app` (ad-hoc signed via `codesign --sign -`, no Apple Developer ID) plus `dist/Tickoshi-macos.zip` (via `ditto`) for release upload. First launch still trips Gatekeeper — right-click → Open or `xattr -d com.apple.quarantine`. Also installs/bundles `certifi`: on macOS Python's OpenSSL can't read the system Keychain, so a darwin-gated block at the top of `Tickoshi.py` points `SSL_CERT_FILE` at certifi's bundle when the default trust store is empty — without it every HTTPS/WSS fetch fails `CERTIFICATE_VERIFY_FAILED`. `certifi` is macOS-only and explicitly `--exclude-module`d in `BUILD.bat`/`BUILD.sh`.
- No test suite, linter, or formatter is configured. There is no `requirements.txt` — the single runtime dependency (`websocket-client`) is installed manually or by the build scripts.

## Architecture

The entire application lives in `Tickoshi.py`. It is a frameless Tkinter widget that composites custom `Canvas` "panels" into a single always-on-top window.

### Rendering model
Each visible cell (digit, currency sign, label, fee block) is a subclass of `tk.Canvas` that draws its own rounded-rectangle card and handles its own animation:
- `DigitPanel` — one digit with a 12-step drum-roll flip (`DRUM_STEPS`, `DRUM_MS`).
- `SignPanel` — the currency symbol card.
- `LabelPanel` — generic two-line label tile used for Sats / Block Height / Halving / Hashrate / Mempool.
- `FeeBlock` — three-row LOW/MED/HIGH sat/vB tile fed by the WebSocket.
- `FlipCard` (top of file) is an older single-card flip primitive still used for secondary displays.

The top-level `Tickoshi(tk.Tk)` class owns layout, the right-click menu, config persistence, drag/lock behavior, and the "accordion" resize logic that adapts the window width to the current digit count and which optional tiles are enabled. Panel dimensions scale from `BASE_PANEL_W/H/FS/R` by the `SIZES` factor (Small/Medium/Large).

### Data sources and threading
Network I/O runs on background threads and hands results to the Tk main loop via a `queue.Queue` polled on the Tk timer — never touch Tk widgets from a worker thread.
- Price: CoinGecko (`_prices_coingecko`) with Binance as the fallback (`_prices_binance`). Deliberately these two only — do not add other price hosts without asking. CoinGecko covers every currency in one request; Binance is per-symbol, so it is only asked for the currency actually on screen, and it answers HTTP 451 to whole countries, so that status abandons the host rather than stalling on the remaining symbols. `CURRENCIES` carries `None` for pairs Binance has delisted (GBP, RUB) — requesting them is a guaranteed `-1121 Invalid symbol`. `_http_get` returns `(body, status)` so callers can tell a geo-block from a bad symbol from no network. Polled on the user's refresh interval (1/5/15/30/60 min; `REFRESH_OPTIONS`) — but a cycle that fetched no live price reschedules on a short escalating backoff instead (`FETCH_RETRY_MIN_S`..`FETCH_RETRY_MAX_S`, see `_next_fetch_delay`). Windows refuses sockets (WinError 10013) for the first seconds of a process's life while the firewall clears the new binary, so the startup fetch routinely fails on a healthy machine; without the retry a 60-min interval left the price blank for an hour.
- Block height: `blockchain.info` (`_fetch_block_height`). Halving days are computed locally by `calc_halving_days`, which derives the next halving from the height via `HALVING_INTERVAL` — don't reintroduce a hardcoded next-halving constant, it turns the tile into `--` forever once that block is mined.
- Hashrate: mempool.space mining REST endpoint (`_fetch_hashrate`).
- Fees + mempool size: persistent WebSocket to `wss://mempool.space/api/v1/ws` via `websocket-client`. Lifecycle is managed by the module-level `_ws_start` / `_ws_stop` / `_ws_run` and the `_ws_on_*` callbacks — the WS reconnects on its own and pushes updates independent of the poll interval. `_ws_run` takes a generation token so a run winding down from an earlier `_ws_stop()` retires instead of blocking a restart; reconnect backoff resets after a connection stays up `WS_STABLE_S`. These two tiles have no HTTP fallback, so they blank to `--` once the socket has been silent for `STALE_AFTER_S` rather than showing a dead feed's last reading as live.

Two invariants hold the loop together, and both were bugs before:
- The queued result is what arms the next cycle, so `_worker` posts it from a `finally` and `_on_fetch_done` reschedules from a `finally`. Never make either conditional on success, or one exception stops the widget refreshing for the rest of the session.
- WebSocket tiles do **not** repaint on the price cycle. `_ws_on_message` sets `_ws_repaint`, and the result poller drains it (and ticks every ~5s so staleness blanking lands too). Read fee values through `_fee_values()` so the liveness gate is applied everywhere.

When adding a new data source, follow the same pattern: fetch on a worker thread, push into the queue, render on the Tk tick.

### Secondary tiles ("modules")
Optional tiles are declared in the `MODULES` list (`key`, menu label). Enabled keys are stored in config as `modules`, rendered in the order the user toggled them on, and auto-paired two-per-row when both fit. Adding a tile means: append to `MODULES`, add a data fetch (or WS handler), and add a render branch in the layout code in `Tickoshi`.

### Config and logs
Settings autosave on every change to a JSON file next to a rolling 200-line debug log (`_debug_log`):
- Windows: `%APPDATA%\Tickoshi\tickoshi_config.json` / `tickoshi_debug.log`
- macOS: `~/Library/Application Support/Tickoshi/` (same filenames)
- Linux: `~/.config/Tickoshi/` (same filenames)

`config_path()` resolves the platform-specific location. `_log_dns_probe` logs what the price hosts resolve to, and `_note_if_intercepted` explains a handshake answered with non-TLS bytes. Together they separate three failure modes that all read as "no network": a local firewall denies the socket outright (WinError 10013 on Windows) and never reaches a handshake; DNS interception resolves the name somewhere unexpected; and filtering on the hostname in the ClientHello lets the connection reach the correct address but returns a malformed handshake (`[SSL: WRONG_VERSION_NUMBER]` under OpenSSL, `SEC_E_INVALID_TOKEN` under Windows schannel). Only the last is unfixable from inside the app. Everything read back is clamped by `_sanitize_config` and the window is kept on-screen by `_apply_position`: values flow straight into Tk (`geometry`, `-alpha`, `after`), the window is frameless with no taskbar entry, and under `--windowed` there is no console — so a bad saved value would otherwise make the app invisible or fail to start with nothing to show for it. The debug log is the tool for diagnosing connectivity: every session opens with a `--- Tickoshi start` line (python/platform/websocket-client versions, CA-store size, which proxy env vars are set), and each network failure logs its HTTP status plus the server's error body, or the transport exception. Because the window is only 200 lines, per-message value logging is deduplicated — a repeated fee/mempool/hashrate value logs once, so connection errors are not scrolled away. Keep new log lines quiet on success for the same reason.

All HTTP fetches go through `_http_get()`, which centralizes the User-Agent, timeout, and that failure logging.

### Packaging note
`BUILD.bat` / `BUILD.sh` / `BUILD.command` aggressively exclude heavy stdlib/third-party modules (numpy, pandas, matplotlib, smtplib, http.server, etc.) to keep the onefile binary small. If you add an import that transitively pulls one of these in, update the exclude list in all three scripts or the build will ship a much larger binary.
