<p align="center">
  <img src="Tickoshi.png" width="100" alt="Tickoshi icon" />
</p>

<h1 align="center">Tickoshi</h1>

<p align="center">
  A live Bitcoin price ticker for your desktop — inspired by the Coinkite BlockClock Mini.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.1-orange?style=flat-square" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey?style=flat-square" />
  <img src="https://img.shields.io/badge/No%20dependencies-zero%20pip%20installs%20to%20run-brightgreen?style=flat-square" />
</p>
<p align="center">
<img width="560" height="180" alt="image" src="https://github.com/user-attachments/assets/81693947-de56-4600-ae6b-e4d06247cba9" />
</p>
---

## Features

- **Live price** — customizable refresh interval (30 sec – 1 hr) via CoinGecko (Binance fallback)
- **Currency sign panel** — displays $, ₺, €, £, ¥, ₽ next to the price
- **Flip animation** — smooth drum-roll transition on every digit change
- **Accordion layout** — panels adjust automatically to match the digit count
- **Price flash** — spark chase animation races around the border on price changes (green = up, red = down)
- **3 view modes** — live BTC price, current block height, or halving countdown
- **6 currencies** — USD · TRY · EUR · GBP · JPY · RUB
- **3 size presets** — Small · Medium · Large
- **3 border colors** — Gold · Orange · White
- **Opacity control** — 50% · 70% · 85% · 100%
- **Always-on-top toggle** — keep it above all windows, or let it blend in
- **Frameless & draggable** — click and drag anywhere to reposition
- **Double-click to copy** — copies current value to clipboard
- **Persistent config** — all settings are remembered between sessions

---

## Running from source

No pip installs required. Just Python 3.10+.

```bash
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

> Both scripts install PyInstaller automatically if it is not already present.

---

## Usage

| Action | How |
|---|---|
| Move widget | Click and drag anywhere |
| Copy value to clipboard | Double-click |
| Change view mode | Right-click → **View** |
| Change size | Right-click → **Size** |
| Change currency | Right-click → **Currency** |
| Change refresh rate | Right-click → **Refresh** |
| Change opacity | Right-click → **Opacity** |
| Change border color | Right-click → **Border** |
| Toggle always-on-top | Right-click → **Always on top** |
| Toggle price flash | Right-click → **Price flash** |
| Close | Right-click → **Close** |

### View modes

| Mode | Sign | Displays |
|---|---|---|
| Price | $ ₺ € £ ¥ ₽ | Live BTC price in selected currency |
| Block Height | # | Current Bitcoin block height |
| Halving | ⏳ | Estimated days until next halving |

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

30 sec · 1 min · 5 min · 15 min · 30 min · 1 hr

---

## Config

Settings are saved automatically when you move, resize, or change any option.

| Platform | Location |
|---|---|
| Windows | `%APPDATA%\Tickoshi\tickoshi_config.json` |
| Linux / macOS | `~/.config/Tickoshi/tickoshi_config.json` |

---

## Tech stack

| | |
|---|---|
| UI | Python / Tkinter (stdlib only) |
| Price API | [CoinGecko](https://www.coingecko.com/) (primary) · Binance (fallback) |
| Block API | [blockchain.info](https://blockchain.info/) |
| Build | [PyInstaller](https://pyinstaller.org/) |
