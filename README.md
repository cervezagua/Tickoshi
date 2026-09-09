<p align="center">
  <img src="Tickoshi.png" width="100" alt="Tickoshi icon" />
</p>

<h1 align="center">Tickoshi</h1>

<p align="center">
  A live Bitcoin price ticker for your desktop — inspired by the Coinkite BlockClock Mini.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.2-orange?style=flat-square" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square" />
</p>
<p align="center">
<img width="463" height="280" alt="image" src="https://github.com/user-attachments/assets/e5c99cbf-b545-4157-b1db-3e740005ea28" />
</p>

## Features

- **Live price** — customizable refresh interval (1 min – 1 hr) via CoinGecko (Binance fallback)
- **Modular secondary tiles** — pick any combination of Fees, Sats, 24h Change, Block Height, Block Age, Halving, Difficulty, Hashrate, Mempool. Tiles stack under the price in the order you enable them and auto-pair two-per-row to keep the widget compact
- **Live mempool feed** — LOW / MED / HIGH priority sat/vB values streamed from mempool.space's WebSocket, matching the site's homepage tiles exactly
- **Hashrate + mempool size** — network health tiles (EH/s and MB) fed from mempool.space
- **24h change** — signed percentage move, arriving on the same request as the price
- **Block age + difficulty retarget** — seconds since the last block, ticking live, and the projected change at the next retarget
- **Start at login** — one menu toggle, registered with the OS (Windows, macOS and Linux)
- **Currency sign panel** — displays $, ₺, €, £, ¥, ₽ next to the price
- **Flip animation** — smooth drum-roll transition on every digit change
- **Accordion layout** — panels adjust automatically to match the digit count
- **Price flash** — the border pulses green (up) or red (down) on price changes
- **6 currencies** — USD · TRY · EUR · GBP · JPY · RUB
- **3 size presets** — Small · Medium · Large
- **3 border colors** — Gold · Orange · White
- **Opacity control** — 50% · 70% · 85% · 100%
- **Always-on-top toggle** — keep it above all windows, or let it blend in
- **Frameless & draggable** — click and drag anywhere to reposition
- **Lock** — freeze position so you don't drag it by accident
- **Double-click to copy** — copies current value to clipboard
- **Persistent config** — all settings are remembered between sessions

---

## Running from source

Requires Python 3.10+ and one pip package:

```bash
pip install websocket-client
python Tickoshi.py
```

> **Linux:** make sure `python3-tk` is installed.
> ```bash
> # Ubuntu / Debian
> sudo apt install python3-tk
> ```

---

## Building a standalone binary

### Windows

Double-click **`BUILD.bat`** or run from a terminal:

```bat
BUILD.bat
```

Produces `dist\Tickoshi.exe` — a single, portable EXE with no Python required.

### Linux

```bash
chmod +x BUILD.sh
./BUILD.sh
```

Produces `dist/Tickoshi` — a single ELF binary. A `.desktop` launcher is also created automatically at `~/.local/share/applications/tickoshi.desktop`.

### macOS

Double-click **`BUILD.command`** in Finder, or run from a terminal:

```bash
chmod +x BUILD.command
./BUILD.command
```

Produces `dist/Tickoshi.app` (ad-hoc signed) and `dist/Tickoshi-macos.zip` (ready for release upload).

> **First launch (Gatekeeper):** macOS will refuse to open the app because it isn't notarized. Right-click the app → **Open** (then confirm), or run:
> ```bash
> xattr -d com.apple.quarantine dist/Tickoshi.app
> ```

> **macOS + HTTPS:** Python's OpenSSL on macOS can't read the system Keychain, so the app falls back to [`certifi`](https://pypi.org/project/certifi/)'s CA bundle (installed and bundled by `BUILD.command` automatically). Builds made before this fix show no data at all — every HTTPS/WSS request failed certificate verification. Rebuild with the current `BUILD.command` to fix.

> All three build scripts install PyInstaller, Pillow, and `websocket-client` automatically if they aren't already present (plus `certifi` on macOS).

---

## Usage

| Action | How |
|---|---|
| Move widget | Click and drag anywhere |
| Copy value to clipboard | Double-click |
| Toggle tiles | Right-click → **View** (multi-select) |
| Change size | Right-click → **Size** |
| Change currency | Right-click → **Currency** |
| Change refresh rate | Right-click → **Refresh** |
| Change opacity | Right-click → **Opacity** |
| Change border color | Right-click → **Border** |
| Toggle always-on-top | Right-click → **Always on top** |
| Toggle price flash | Right-click → **Price flash** |
| Start with the computer | Right-click → **Start at login** |
| Lock position | Right-click → **Lock** |
| Close | Right-click → **Close** |

### Secondary tiles

The main row always shows the live BTC price. Enable any of these optional tiles from the **View** menu to stack them underneath. Tiles appear in the order you toggle them on, and auto-pair two-per-row when both fit.

| Tile | Shows | Source |
|---|---|---|
| **Fees** | Low / Medium / High priority (sat/vB) | mempool.space WebSocket |
| **Sats** | Sats per unit of selected currency | Computed from price |
| **24h Change** | Signed % move over 24 hours | CoinGecko (same request as the price) |
| **Block Height** | Current block number | mempool.space WebSocket, blockchain.info fallback |
| **Block Age** | Time since the last block, ticking | mempool.space WebSocket |
| **Halving** | Days until next halving | Computed from block height |
| **Difficulty** | Projected % change at the next retarget | mempool.space WebSocket |
| **Hashrate** | Network hashrate (EH/s, 3-day avg) | mempool.space |
| **Mempool** | Unconfirmed vBytes (MB) | mempool.space WebSocket |

> The 24h Change tile reads `--` when the price came from the Binance fallback: that endpoint carries no 24h figure, and a stale percentage beside a fresh price would be worse than none.

### Supported currencies

| Code | Pair |
|---|---|
| USD | BTC / US Dollar |
| TRY | BTC / Turkish Lira |
| EUR | BTC / Euro |
| GBP | BTC / British Pound |
| JPY | BTC / Japanese Yen |
| RUB | BTC / Russian Ruble |

### Refresh intervals

1 min · 5 min · 15 min · 30 min · 1 hr

Fees, mempool size, block height/age and difficulty push over a persistent WebSocket and update independently of this interval. A cycle that fetches no live price retries within seconds rather than waiting out the whole interval.

---

## Config

Settings are saved automatically when you move, resize, or change any option.

| Platform | Location |
|---|---|
| Windows | `%APPDATA%\Tickoshi\tickoshi_config.json` |
| macOS | `~/Library/Application Support/Tickoshi/tickoshi_config.json` |
| Linux | `~/.config/Tickoshi/tickoshi_config.json` |

A rolling `tickoshi_debug.log` (last 200 lines) sits alongside the config for troubleshooting the WebSocket feed.

---

## Tech stack

| | |
|---|---|
| UI | Python / Tkinter |
| Dependency | [`websocket-client`](https://pypi.org/project/websocket-client/) |
| Price API | [CoinGecko](https://www.coingecko.com/) (primary) · Binance (fallback) |
| Block height | [blockchain.info](https://blockchain.info/) |
| Fees + mempool size | [mempool.space WebSocket](https://mempool.space/docs/api/websocket) (`wss://mempool.space/api/v1/ws`) |
| Hashrate | [mempool.space mining API](https://mempool.space/docs/api/rest#get-hashrate) |
| Build | [PyInstaller](https://pyinstaller.org/) |

---

## Release notes

### 1.2

**New**
- Three tiles: **24h Change**, **Block Age** (ticks live) and **Difficulty** (projected retarget). The latter two cost no extra requests — the data was already arriving on the mempool.space socket and being discarded.
- **Start at login** toggle — `Run` registry value on Windows, LaunchAgent on macOS, autostart `.desktop` on Linux. Read back from the OS, so the checkmark can't disagree with reality.
- Block height now lands the moment a block is mined, instead of on the next HTTP poll.

**Fixed**
- Price could stay blank until restart. A CoinGecko response that carried no usable quote still counted as success, so the Binance fallback never ran.
- A failed fetch waited out the whole refresh interval — up to an hour of dashes after a single rate limit. Now retries in seconds.
- Toggling a WebSocket tile off and back on killed fees/mempool/hashrate for the rest of the session; reconnect backoff also never reset after a sleep/wake.
- One exception in a fetch or a redraw stopped all future refreshes, silently.
- The debug log filled its whole 200-line window in about two minutes, scrolling out every error just when it was needed.
- The Halving tile would have read `--` forever from block 1,050,000; the next halving is now derived rather than hardcoded.
- `BTC/GBP` and `BTC/RUB` were requested from Binance on every fallback despite being delisted, returning `Invalid symbol` each time.

### 1.1

- **macOS support.** New `BUILD.command` produces an ad-hoc signed `Tickoshi.app` bundle.
- Config stored at `~/Library/Application Support/Tickoshi/` on macOS; Ctrl-click / two-finger-click open the menu.

### 1.0

Initial release. Live BTC price in 6 currencies; modular secondary tiles (Fees, Sats, Block Height, Halving, Hashrate, Mempool) that stack and auto-pair two-per-row; live mempool.space WebSocket feed for fees and mempool size; 3 size presets, 3 border themes, opacity, always-on-top, lock, price flash, and persistent config. Windows and Linux builds.
