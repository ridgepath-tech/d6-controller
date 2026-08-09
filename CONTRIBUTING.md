# Contributing

Thanks for making the D6 more useful. This is a Windows-first, hardware-facing
project, so small, testable changes are especially valuable.

## Start a development checkout

```powershell
git clone https://github.com/ridgepath-tech/d6-controller.git
cd d6-controller
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
```

Use `install.ps1` when you want the complete installed experience, including
the startup task. Use the commands above when you only want to develop and
test. Never copy your real `profiles/default.json`, `data/auth.json`,
`creds.txt`, or a `.d6config` backup into a pull request.

## Before opening a change

Run the same checks used by CI:

```powershell
python scripts/public_release_check.py
python -m unittest discover -v
npm run build
git diff --check
```

To exercise the distributable build, install Inno Setup 6 and run
`.\packaging\build-installer.ps1 -Version 0.3.0`. The generated bundle and
installer are ignored build output and must not be committed.

If you have a D6 attached, verify hardware changes manually after the mocked
tests pass. Do not run the vendor Control Deck and this service against the
same D6 at the same time.

## Change guidelines

- Keep the service bound to `127.0.0.1` unless the change includes a separate
  threat model and explicit authorization design.
- Do not add credentials, personal paths, machine-specific executable paths,
  generated local artwork, logs, or device captures to the repository.
- Keep protocol claims tied to observed behavior and update
  `PROTOCOL_NOTES.md` when a command is newly verified.
- Add a regression test for behavior changes. Device I/O tests should use
  fakes or captured reports rather than requiring hardware in CI.
- Preserve the clean-room boundary: do not copy vendor binaries, proprietary
  source, firmware, or undocumented private data into the project.
- Keep UI changes accessible and explain new actions in the README.

## Useful development commands

```powershell
# Run the direct CLI without starting the web service
python .\d6_controller.py info
python .\d6_controller.py transport-info

# Run the local service after building the UI
python .\d6_service.py

# Run the Vite development UI in a second terminal
npm run dev
```

The service is deliberately local-only and is not a hosted application. A
contribution that changes that assumption needs security review before merge.
