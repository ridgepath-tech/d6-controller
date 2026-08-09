# Architecture

This project is a local Windows application with three deliberately separate
layers:

```text
React/Vite UI
    │ localhost HTTP + authenticated cookie / SSE events
    ▼
d6_service.py
    │ serialized device writes + profile/action dispatch
    ▼
d6_controller.py
    │ HID or compatible WinUSB transport
    ▼
FIFINE AmpliGame D6
```

## Runtime components

- `d6_controller.py` is the low-level device library and CLI. It discovers the
  D6 by VID/PID, opens the verified transport, sends command frames, uploads
  100x100 LCD images, and decodes button reports.
- `d6_service.py` owns the device handle. It reconnects when the device is
  unplugged, sends the heartbeat, listens for reports, dispatches configured
  actions, serves the API, and monitors Windows display/system power changes.
- `d6_auth.py` stores only a salted password verifier and manages localhost
  sessions. It never belongs in a profile backup.
- `src/main.jsx` and `src/styles.css` are the browser configurator. The built
  files in `dist/` are generated and ignored.
- `profiles/` is the user-owned configuration boundary. The example profile
  and bundled artwork are public; a real `profiles/default.json` is not.

## Request and action flow

1. The UI calls an authenticated localhost endpoint.
2. The service validates the request, loads and normalizes the profile, and
   acquires its operation lock.
3. A built-in action either changes the active page, sends a Windows input,
   opens a user-selected target, or sends a D6 command.
4. Page application clears the deck first, sets brightness, then uploads the
   selected artwork. Empty keys do not retain artwork from an older page.
5. The service emits an SSE event for visible device and action activity.

## Power synchronization

The service creates a hidden Windows window and registers for console-display
power changes. It also handles Windows suspend/resume broadcasts. Display and
system sleep are tracked as separate reasons so overlapping notifications do
not cause duplicate sleep or wake cycles. On wake, the service sends the D6
display wake command and heartbeat, then reapplies the active page.

## Profile model

`profile.example.json` is the canonical public starting point. A profile has:

- `device`: VID/PID metadata and default brightness;
- `scenes`: named groups of pages; and
- each page's `keys`: optional key definitions containing an action and
  optional artwork path.

Actions are intentionally explicit. The project does not discover arbitrary
shortcuts from a user's computer and does not hardcode a desktop application
path. A user may configure a local app or folder path through the UI.

## Extension points

- Add a device command or report decoder in `d6_controller.py` and cover it
  with protocol tests.
- Add a service action in `_dispatch_key` and add a mocked service test.
- Add profile fields through the normalization and validation helpers so old
  profiles remain readable.
- Add UI controls in `src/main.jsx`; keep generated artwork and API behavior
  covered by tests.
- Keep new persistent or sensitive data outside tracked files and update both
  `.gitignore` and `SECURITY.md` when adding a local-state boundary.

The protocol is only partially verified on this hardware. Contributions must
not infer firmware-update, flash, RGB, or undocumented voice-control behavior
from an unrelated StreamDock model.
