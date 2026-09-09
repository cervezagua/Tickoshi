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
- Price: CoinGecko primary, Binance fallback (`_fetch_all_prices`). Polled on the user's refresh interval (1/5/15/30/60 min; `REFRESH_OPTIONS`) — but a cycle that fetched no live price reschedules on a short escalating backoff instead (`FETCH_RETRY_MIN_S`..`FETCH_RETRY_MAX_S`, see `_next_fetch_delay`), so one rate limit doesn't blank the price for a whole interval. Success means a usable quote for the currency on screen, not merely an HTTP 200: CoinGecko answers 200 with an error-shaped body when throttling, and treating that as success skipped the fallback entirely. `CURRENCIES` carries `None` for pairs Binance has delisted (GBP, RUB) — requesting them is a guaranteed `-1121 Invalid symbol`. The CoinGecko call also carries `include_24hr_change=true`, so the 24h move for every currency arrives in the same request — Binance has no equivalent, so the 24h tile reads `--` whenever the fallback supplied the price.
- Block height: `blockchain.info` (`_fetch_block_height`). Halving days are computed by `calc_halving_days`, which derives the next halving from the height via `HALVING_INTERVAL` — don't reintroduce a hardcoded next-halving constant, it turns the tile into `--` forever once that block is mined.
- Hashrate: mempool.space mining REST endpoint (`_fetch_hashrate`).
- Fees, mempool size, difficulty retarget and block height/age: one persistent WebSocket to `wss://mempool.space/api/v1/ws` via `websocket-client`. The socket pushes more than the app once read — `da` carries `difficultyChange`/`remainingBlocks` beside the hashrate, and `blocks` (an array on connect) plus `block` (one per new block) carry height and timestamp. Height from the socket lands the moment a block is mined; `_fetch_block_height` stays as the HTTP fallback and never rolls the height backwards. Lifecycle is managed by the module-level `_ws_start` / `_ws_stop` / `_ws_run` and the `_ws_on_*` callbacks. `_ws_run` takes a generation token so a run winding down from an earlier `_ws_stop()` retires instead of blocking a restart — checking only `is_alive()` there left the feed dead for the session when a tile was toggled off and straight back on. Reconnect backoff resets after a connection holds for `WS_STABLE_S`.

One invariant holds the loop together, and both halves of it were bugs before: the queued result is what arms the next cycle, so `_worker` posts it from a `finally` and `_on_fetch_done` reschedules from a `finally`. Never make either conditional on success — the result poller swallows what is raised in the callback, so one exception used to stop the widget refreshing for the rest of the session with nothing in the log.

When adding a new data source, follow the same pattern: fetch on a worker thread, push into the queue, render on the Tk tick.

### Start at login
`autostart_enabled()` / `set_autostart()` register the app for login: a `Run` registry value on Windows, a LaunchAgent plist on macOS, an autostart `.desktop` on Linux. The state is read back from the OS every time the menu opens rather than mirrored into our config, so an entry removed behind the app's back shows as off. `_launch_command()` returns the executable alone when frozen (PyInstaller sets `sys.frozen`) and interpreter-plus-script otherwise.

### Price alert
One-shot by design. `_arm_alert` captures the direction by comparing the target to the price on screen, so "alert me at 120k" means above when currently below and below when currently above — no direction picker. `_check_price_alert` runs on every price update, fires once (bell plus an amber border pulse, distinct from the green/red move flashes) and disarms, because a widget that beeps every refresh while the price sits past the line gets switched off. `parse_price_input` accepts grouping separators and a currency symbol, and is also what validates the value loaded from config, so a corrupt entry cannot break startup.

### Secondary tiles ("modules")
Optional tiles are declared in the `MODULES` list (`key`, menu label). Enabled keys are stored in config as `modules`, rendered in the order the user toggled them on, and auto-paired two-per-row when both fit. Adding a tile means: append to `MODULES`, add a data fetch (or WS handler), and add a render branch in the layout code in `Tickoshi`.

### Config and logs
Settings autosave on every change to a JSON file next to a rolling 200-line debug log (`_debug_log`):
- Windows: `%APPDATA%\Tickoshi\tickoshi_config.json` / `tickoshi_debug.log`
- macOS: `~/Library/Application Support/Tickoshi/` (same filenames)
- Linux: `~/.config/Tickoshi/` (same filenames)

`config_path()` resolves the platform-specific location. The debug log is primarily for diagnosing the WebSocket feed. Because the window is only 200 lines, per-message value logging is deduplicated via `_ws_logged` — a repeated fee/mempool/hashrate value logs once. Without that, fee pushes filled the whole window in about two minutes and scrolled every connection error out of it. Keep new log lines quiet on success for the same reason.

### Live tile tick
`_start_live_tick` repaints the secondary tiles once a second. The block-age counter has to advance with no new data to prompt it, and `set_value()` is a no-op unless the rendered text changed, so this costs a few string comparisons per second. It also keeps socket-fed tiles current instead of leaving them until the next price cycle, which on a 60-minute interval is an hour away.

### Packaging note
`BUILD.bat` / `BUILD.sh` / `BUILD.command` aggressively exclude heavy stdlib/third-party modules (numpy, pandas, matplotlib, smtplib, http.server, etc.) to keep the onefile binary small. If you add an import that transitively pulls one of these in, update the exclude list in all three scripts or the build will ship a much larger binary.
