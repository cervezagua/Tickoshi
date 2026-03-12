<p align="center">
  <img src="Tickoshi.png" width="100" alt="Tickoshi icon" />
</p>

<h1 align="center">Tickoshi</h1>

<p align="center">
  A live Bitcoin price widget for your desktop — inspired by the Coinkite BlockClock Mini.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey?style=flat-square" />
  <img src="https://img.shields.io/badge/No%20dependencies-zero%20pip%20installs%20to%20run-brightgreen?style=flat-square" />
</p>
<p align="center">
<img width="560" height="138" alt="image" src="https://github.com/user-attachments/assets/f3c2736f-3126-4478-96aa-0f01d9e3d370" />
</p>

---

## Features

- **Live price** — customizable refresh interval (30 sec – 1 hr) via CoinGecko (Binance fallback)
- **Flip animation** — smooth drum-roll transition on every digit change
- **Accordion layout** — panels adjust automatically to match the digit count of the current price
- **6 currencies** — USD · TRY · EUR · GBP · JPY · RUB
- **3 size presets** — Small · Medium · Large
- **Always-on-top toggle** — keep it above all windows, or let it blend in
- **Frameless & draggable** — click and drag anywhere to reposition
- **Persistent config** — position, size, currency, refresh rate, and always-on-top are remembered

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

Produces `dist/Tickoshi` — a single ELF binary. A `.desktop` launcher is also created automatically at `~/.local/share/applications/Tickoshi.desktop`.

> Both scripts install PyInstaller automatically if it is not already present.

---

## Usage

| Action | How |
|---|---|
| Move widget | Click and drag anywhere |
| Change size | Right-click → **Size** |
| Change currency | Right-click → **Currency** |
| Change refresh rate | Right-click → **Refresh** |
| Toggle always-on-top | Right-click → **Always on top** |
| Close | Right-click → **Close** |

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

Settings are saved automatically when you move, resize, or close the widget.

| Platform | Location |
|---|---|
| Windows | `%APPDATA%\BtcWidget\btc_config.json` |
| Linux / macOS | `~/.config/BtcWidget/btc_config.json` |

---

## Tech stack

| | |
|---|---|
| UI | Python / Tkinter (stdlib only) |
| Price API | [CoinGecko](https://www.coingecko.com/) (primary) · Binance (fallback) |
| Build | [PyInstaller](https://pyinstaller.org/) |
