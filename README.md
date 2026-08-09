# D6 Controller

An independent, local Windows controller and browser configurator for the
FIFINE AmpliGame D6 Stream Controller. It lets D6 owners edit profiles, put
artwork on the fifteen LCD keys, create pages and scenes, dispatch Windows
actions, and extend the controller without depending on the vendor UI.

This is a community-oriented hardware project. The repository contains only
shareable code, templates, tests, and bundled artwork. Each user's profile,
password verifier, artwork, logs, and backup files stay on that user's
computer and are ignored by Git.

## What it does

- Controls the verified D6 over HID, with compatible WinUSB support available.
- Sends the vendor-compatible heartbeat and reconnects after device changes.
- Applies brightness and 100x100 LCD artwork to all fifteen keys.
- Includes an experimental, explicitly confirmed 800x480 boot-logo uploader.
- Captures physical press/release events and dispatches configured actions.
- Provides scenes, pages, navigation, page controls, and a visual key editor.
- Supports website, folder, app/file, hotkey/text, microphone mute, and deck
  sleep actions.
- Keeps password-entry text out of generated LCD artwork, while still allowing
  the user to configure local text-entry actions.
- Synchronizes D6 display sleep with Windows console-display and system power
  state, then wakes and reapplies the active page when Windows becomes active.
- Provides authenticated localhost configuration, SSE live events, and
  transactional `.d6config` profile/artwork backup and restore.

## What it does not do

- It is not a hosted service or a remote-control server. The HTTP service binds
  to `127.0.0.1` only.
- It does not include or require a vendor application at runtime. Do not run
  both applications against the same D6 simultaneously.
- It does not expose firmware-update or flash operations.
- Boot-logo replacement is persistent and experimental; the current tool cannot
  back up or restore the factory FIFINE logo.
- RGB/lamp control is not verified on this D6 and is reported as unsupported.
- It does not hardcode a desktop app, Codex installation, personal folder, or
  computer-specific shortcut.
- It does not claim that every StreamDock-family command works on every D6
  firmware. Verified behavior is documented in [PROTOCOL_NOTES.md](PROTOCOL_NOTES.md).

See [SECURITY.md](SECURITY.md) for the local security boundary and sensitive
data rules. See [ARCHITECTURE.md](ARCHITECTURE.md) for the code structure.

## Confirmed device facts

- HID VID/PID: `3142:0007`
- Windows output report: 513 bytes (`report ID 0` plus 512 data bytes)
- A `CRT` command frame is accepted and returns an `ACK ... OK` report.
- Brightness and refresh commands were accepted.
- A vendor-compatible 15-byte `CONNECT` heartbeat was reconstructed and
  accepted by the device.
- Per-key LCD upload was accepted as a 100x100 JPEG sent in 512-byte chunks.
- Physical press/release reports were captured for hardware IDs 1-15.

The key map and image command remain a clean-room compatibility layer until
more D6 models and firmware versions are verified. Do not infer undocumented
features from a different device.

## Requirements

For the installed application:

- Windows 10 or newer;
- Python 3.11 or newer;
- Node.js/npm (a current Node.js LTS is recommended); and
- a connected FIFINE AmpliGame D6.

The low-level protocol tests and frontend build can run without a connected
D6. Hardware commands require Windows and the device.

## Install for normal use

For the simplest experience, download `D6ControllerSetup.exe` from the
project's GitHub Releases page and run it. The installer includes the Python
runtime dependencies, service executable, frontend, public template, and
bundled artwork; users do not need Python, Node.js, or a separate vendor
application. It installs per-user, registers launch at logon, preserves local
profiles during upgrades, and opens the configurator after installation.

The installer is built for Windows x64. The D6 must still be connected to the
computer, and Windows may need to finish installing its normal HID device
driver.

If a release installer is not available, use the developer setup below.

Open PowerShell in the repository folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

The installer creates a private Python environment, installs dependencies,
builds the frontend, creates `profiles/default.json` from the public example,
and registers automatic launch for the current Windows user. Existing local
profiles are preserved. If scheduled-task creation is blocked, the installer
uses the current-user startup entry instead.

Open <http://127.0.0.1:8765/> and create a local administrator password of at
least ten characters. Only a salted password verifier is stored. To remove
automatic launch without deleting profiles or installed files:

```powershell
.\uninstall.ps1
```

## Run without installing startup

Build and start the local service manually:

```powershell
npm ci
npm run build
python .\d6_service.py
```

Then open <http://127.0.0.1:8765/>. For frontend development, run the service
in one terminal and `npm run dev` in another; Vite proxies `/api` to port
8765. The service itself remains localhost-only.

## Configure the D6

1. Click a key in the fifteen-key layout.
2. Choose an action, optional LCD label, and font size.
3. Upload custom artwork if desired; generated artwork is sized for the D6.
4. Click **Save**, then **Apply selected page**.
5. Create additional scenes/pages when you want separate layouts.

Available action families include:

- Back, Home, Previous Page, Next Page, Page Indicator, and Sleep;
- microphone mute, with bundled blue/red state artwork;
- Navigate to a scene/page;
- Open Website;
- Open Folder through a native folder picker;
- Open an app, file, or folder using a locally chosen target; and
- Send hotkeys or text.

Configured keys can be dragged to another key. Replacing an existing key
requires confirmation. Settings provides password rotation, sign-out, and
`.d6config` backup/restore. Treat profiles and backups as sensitive if they
contain password-entry actions or personal artwork.

The starter profile includes a small internal Codex page with a Home action.
It intentionally does not guess the path or capabilities of a user's desktop
installation; configure any local application action yourself.

## Direct CLI

The direct controller is useful for diagnostics and protocol experiments:

```powershell
python .\d6_controller.py info
python .\d6_controller.py transport-info
python .\d6_controller.py brightness 42
python .\d6_controller.py heartbeat
python .\d6_controller.py image 1 .\my-key-art.jpg
python .\d6_controller.py boot-logo .\my-logo.png --confirm-write
python .\d6_controller.py clear-key 1
python .\d6_controller.py scene .\profile.example.json --scene default --page main
python .\d6_controller.py listen --seconds 30
```

The image command resizes/crops to 100x100, rotates the image 180 degrees,
and uploads it as JPEG data. `D6_TRANSPORT=hid` forces HID; `winusb` requires a
WinUSB binding; `auto` tries WinUSB and falls back to HID. The default is
`auto`.

The boot-logo command fits an image to an 800x480 white canvas and uploads it
to persistent device storage. The explicit flag is required because the
factory logo cannot currently be read back by this project. Restart the D6 to
observe the result, and do not run the command while the FIFINE Control Deck is
using the device. Boot-logo upload is intentionally separate from profile
application.

## Development

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
python scripts/public_release_check.py
python -m unittest discover -v
npm run build
git diff --check
```

To build the standalone bundle and installer locally, install Inno Setup 6 and
run:

```powershell
.\packaging\build-installer.ps1 -Version 0.3.0
```

The standalone bundle is written under `packaging/out/` and the installer is
written under `artifacts/`; both locations are ignored. GitHub Actions builds
the same installer on demand or when a `v*` tag is pushed.

The public-release check scans tracked and non-ignored files for local-only
paths and common credential formats without printing matching content. CI runs
the same check, Python tests, and frontend build on Windows. Read
[CONTRIBUTING.md](CONTRIBUTING.md) before opening a change.

## Repository map

| Path | Purpose |
| --- | --- |
| `d6_controller.py` | Low-level transport, protocol frames, CLI, and report decoding |
| `d6_service.py` | Local HTTP/SSE service, action dispatch, reconnect, and power sync |
| `d6_auth.py` | Local password verifier and session management |
| `src/` | React/Vite configurator |
| `profile.example.json` | Safe public profile template |
| `profiles/assets/` | Bundled public artwork only |
| `test_*.py` | Protocol and service regression tests |
| `PROTOCOL_NOTES.md` | Observed hardware behavior and confidence boundaries |
| `SECURITY.md` | Sensitive-data and localhost-boundary guidance |
| `ARCHITECTURE.md` | Runtime data flow and extension points |
| `CONTRIBUTING.md` | Setup, checks, and contribution rules |

## Local data and public-release safety

Never commit:

- `profiles/*.json` or custom `profiles/assets/*` artwork;
- `data/auth.json` or other `data/` files;
- `creds.txt`, `.env` files, private keys, or local tokens;
- `.d6config` backups;
- logs, device captures, disassembly files, or generated bundles; or
- personal executable, folder, or shortcut paths.

Run `python scripts/public_release_check.py` before publishing. If a secret
has ever been committed, removing it from the current files is not enough:
rotate it and follow Git hosting guidance to remove the historical exposure.

## License and attribution

This project is independent third-party software and is not affiliated with or
endorsed by FIFINE or OpenAI. Product and company names identify compatible
hardware and services; all marks remain the property of their owners. See
[LICENSE](LICENSE).
