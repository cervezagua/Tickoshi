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
- Price: CoinGecko primary, Binance fallback (`_fetch_all_prices`). Polled on the user's refresh interval (1/5/15/30/60 min; `REFRESH_OPTIONS`).
- Block height: `blockchain.info` (`_fetch_block_height`). Halving days are computed locally against `NEXT_HALVING_BLOCK = 1_050_000`.
- Hashrate: mempool.space mining REST endpoint (`_fetch_hashrate`).
- Fees + mempool size: persistent WebSocket to `wss://mempool.space/api/v1/ws` via `websocket-client`. Lifecycle is managed by the module-level `_ws_start` / `_ws_stop` / `_ws_run` and the `_ws_on_*` callbacks — the WS reconnects on its own and pushes updates independent of the poll interval.

When adding a new data source, follow the same pattern: fetch on a worker thread, push into the queue, render on the Tk tick.

### Secondary tiles ("modules")
Optional tiles are declared in the `MODULES` list (`key`, menu label). Enabled keys are stored in config as `modules`, rendered in the order the user toggled them on, and auto-paired two-per-row when both fit. Adding a tile means: append to `MODULES`, add a data fetch (or WS handler), and add a render branch in the layout code in `Tickoshi`.

### Config and logs
Settings autosave on every change to a JSON file next to a rolling 200-line debug log (`_debug_log`):
- Windows: `%APPDATA%\Tickoshi\tickoshi_config.json` / `tickoshi_debug.log`
- macOS: `~/Library/Application Support/Tickoshi/` (same filenames)
- Linux: `~/.config/Tickoshi/` (same filenames)

`config_path()` resolves the platform-specific location. The debug log is primarily for diagnosing the WebSocket feed.

### Packaging note
`BUILD.bat` / `BUILD.sh` / `BUILD.command` aggressively exclude heavy stdlib/third-party modules (numpy, pandas, matplotlib, smtplib, http.server, etc.) to keep the onefile binary small. If you add an import that transitively pulls one of these in, update the exclude list in all three scripts or the build will ship a much larger binary.
