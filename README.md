# D6 Controller

This repository contains an independent local Windows controller for the
FIFINE AmpliGame D6 Stream Controller. It provides a browser-based profile
editor, page navigation, per-button LCD artwork, hotkey/password entry,
website/app/folder actions, Codex-oriented controller pages, and live device
events.
It controls the device directly and does not require the vendor application to
be running at the same time.

The project is designed as a local service. It binds to `127.0.0.1` only;
see [SECURITY.md](SECURITY.md) before changing that boundary.

## Confirmed device facts

- HID VID/PID: `3142:0007`
- Windows output report: 513 bytes (`report ID 0` plus 512 data bytes)
- A `CRT` command frame is accepted and returns an `ACK ... OK` report.
- Brightness and refresh commands were accepted.
- The vendor-compatible 15-byte `CONNECT` heartbeat was reconstructed from
  the installed library and accepted by the device.
- Per-key LCD upload was accepted as a 100x100 JPEG sent in 512-byte chunks.
- Physical press/release reports were captured for hardware IDs 1-15. The
  tested key-ID map is recorded in `d6_controller.py`.

The key ID map and image command are treated as a compatible StreamDock-family
protocol until every D6 event and image mode is verified on this specific unit.
The controller deliberately does not send firmware-update or flash commands.

## First-time setup

On Windows with Python 3.11+ and Node.js/npm installed, run PowerShell from
the repository folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

The installer creates a private Python environment, installs dependencies,
builds the web interface, creates an empty `profiles/default.json` from the
public template, and registers a `D6 Controller` task to start the service when
the current user logs on. If local policy prevents a standard user from
creating a task, it automatically uses the current-user `HKCU` startup entry
instead. Existing local profiles are preserved.

The first visit opens an in-app administrator setup screen. Choose a local
password of at least 10 characters; only a salted `scrypt` verifier is stored.
The service remains bound to `127.0.0.1`, and authenticated configuration
requests use an HttpOnly, SameSite session cookie.

To remove only the automatic launch task:

```powershell
.\uninstall.ps1
```

The profile and installed files are intentionally left in place.

## Manual usage

Run from this directory in PowerShell:

```powershell
python .\d6_controller.py info
python .\d6_controller.py brightness 42
python .\d6_controller.py heartbeat
python .\d6_controller.py qucmd 0x01 0x02 0x03 0x04 0x05
python .\d6_controller.py image 1 .\my-key-art.jpg
python .\d6_controller.py clear-key 1
python .\d6_controller.py scene .\profile.example.json --scene default --page main
python .\d6_controller.py listen --seconds 30
python -m unittest .\test_d6_controller.py
```

The image command resizes/crops to 100x100, rotates the image 180 degrees, and
uploads it as JPEG data. The scene command applies a selected scene/page's
images while leaving each key's action metadata in the profile for a separate
dispatcher. See `profile.example.json` for the profile shape.

For a simple visual profile loader and event monitor, run:

```powershell
python .\d6_app.py
```

## Local web controller

The project also includes a localhost-only service and React web frontend. The
service owns the HID handle, listens for D6 press/release events, serves saved
profiles, and applies brightness plus 100x100 LCD artwork. The frontend keeps
profile, scene, and page structure in JSON while uploading artwork into the
profile's `assets` folder.

Build and run the standalone local app from PowerShell:

```powershell
npm install
npm run build
python .\d6_service.py
```

Then open <http://127.0.0.1:8765/>. For frontend development, run the Python
service in one terminal and `npm run dev` in another; Vite proxies `/api` to
the service on port 8765.

The service intentionally binds only to localhost. The installed FIFINE
Control Deck remains available as a fallback, but both applications should not
be used to control the D6 at the same time. RGB is shown as unsupported because
this unit does not expose lamp control.

The service probes WinUSB first and falls back to the verified HID transport.
Set `D6_TRANSPORT=hid` to force HID or `D6_TRANSPORT=winusb` to require a
WinUSB driver binding. Run `python .\\d6_controller.py transport-info` to
inspect the available interface paths and endpoint binding without sending
device commands.

### Configure a key

Click any key in the visual 15-key layout. The key opens its complete editor,
including action selection, labels, font size, generated LCD preview, artwork
upload/replacement/reset, and Delete Action. Available actions include:

- built-in Back, Home, Previous, Next, Page Indicator, deck Sleep, and microphone mute;
- Navigate to scene/page;
- Open Website;
- Open Folder, using a native Windows folder picker;
- Open app, file, or folder through the generic launch action; and
- Send hotkey or text, including password-entry text that is never rendered as
  the literal secret on the LCD.

Generated artwork is designed for the D6's 100×100 LCD and is replaced by
custom artwork when supplied. Labeled Open Folder actions preserve the custom
folder artwork and draw the label over it. Apply the selected page to send the
full layout to the deck. LCD labels use a multiline editor and preserve
explicit line breaks.

### Codex page

The public starter profile includes an internal `Codex` page rather than a
website redirect. It provides the common deck controls (Home, Back, Previous,
Next, Page Indicator, and Sleep). The installed Codex desktop executable is
machine-specific, so an optional Open Codex launch action should be configured
locally after verifying the installed application path. No undocumented voice,
accept/reject, or agent shortcut is hardcoded.

### Settings and backups

Settings provides password changes, sign out, and a portable `.d6config`
backup/restore flow. Backups include profiles and LCD artwork, are schema
versioned, and are restored transactionally after ZIP path and structure
validation. Backups may contain password-entry actions, so store them securely.

## Current limitations

The connected D6 reports no lamp-control capability through the installed
vendor library, and the vendor settings page exposes no RGB controls. RGB is
therefore reported as unsupported for this unit; the generic QUCMD primitive
is retained only for future firmware/device comparison. The controller
deliberately does not send firmware-update or flash commands.

RidgePath D6 Controller is an independent third-party project and is not
affiliated with or endorsed by FIFINE or OpenAI. Product and company names are
used only to identify compatible hardware and services. All marks remain the
property of their respective owners.
