# D6 protocol notes

These notes describe behavior observed on the connected FIFINE D6, not a
firmware modification. The installed Control Deck remains available as a
fallback.

## Transport

- HID VID/PID: `3142:0007`
- Windows report size: 513 bytes: report ID `0` plus 512 data bytes
- The device accepts `CRT` command frames and returns `ACK ... OK` reports.
- The direct controller uses overlapped Windows HID I/O and does not claim the
  device exclusively.
- The controller includes an optional WinUSB transport. This D6 currently
  exposes the USB interface through `HidUsb`, and `WinUsb_Initialize` reports
  Windows error 31, so the service safely falls back to HID. The WinUSB path
  will be usable automatically if a compatible driver binding is present.
- HID initialization requests a 64-report input queue, matching the vendor
  library's `HidD_SetNumInputBuffers` setup.

## Physical keys

The D6 reports both press (`state=1`) and release (`state=0`) events. The
hardware ID is at data offset 9 and state at data offset 10. The logical key
mapping follows the physical labels: keys 1–5 are the bottom row, 6–10 are
the middle row, and 11–15 are the top row.

| Logical key | Hardware ID |
| ---: | ---: |
| 1–5 | 0x0B–0x0F |
| 6–10 | 0x06–0x0A |
| 11–15 | 0x01–0x05 |

All 15 IDs were observed during a physical capture.

## LCD graphics

- Tested upload format: 100x100 JPEG
- The `BAT` header carries the JPEG length as a two-byte big-endian value,
  followed by the device key ID; the D6 acknowledges malformed headers too,
  so the header shape must be exact before treating an upload as successful.
- The controller fits/crops, rotates 180 degrees, and uploads the image in
  512-byte chunks.
- A per-key image upload was accepted by the device and followed by a refresh.
- The vendor settings identify the boot-logo canvas as 800x480; that is
  separate from the per-key 100x100 artwork path.

## Host-side profiles

Scenes and pages are represented in the vendor application and applied to the
device by the host. `profile.example.json` models that separation: each key
has image metadata and an action object, while `d6_controller.py scene` applies
only the selected page's images. Action dispatch can be added without changing
the HID transport.

## Other commands

- Brightness, wake, refresh, and clear commands are implemented.
- The vendor library's 15-byte `CONNECT` heartbeat was reconstructed and sent
  successfully.
- The vendor library exposes a generic five-byte QUCMD primitive, which is
  available as `send_qucmd`/`qucmd`, but the D6's settings UI does not expose
  RGB controls. The library's lamp-control capability flag is also false after
  opening this D6. RGB is therefore reported as unsupported; QUCMD parameter
  semantics remain unconfirmed and the controller does not guess at them.

The controller deliberately does not send firmware-update or flash commands.
