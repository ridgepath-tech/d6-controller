# Security notes

The controller is intended to run locally on Windows and binds its HTTP
service to `127.0.0.1` only. It is not an internet-facing service. The service
also validates localhost Host/Origin requests and protects configuration,
profile, artwork, folder-dialog, and backup endpoints with an authenticated
session.

On first launch, the administrator creates a password in the in-app setup
screen. The service stores only a random-salt `hashlib.scrypt` verifier. Login
sessions use cryptographically random HttpOnly cookies with SameSite protection
and an eight-hour expiration. Failed logins are rate-limited, and changing the
password invalidates existing sessions.

Password-entry actions are stored in the local profile as text so Windows can
emit them through `SendInput`. Treat `profiles/default.json` and any local
credential source as sensitive. They are intentionally excluded from the
public repository. Do not commit credentials, generated personal artwork,
logs, or exported device files.

Portable `.d6config` backups contain profiles and referenced/generated artwork,
but not the admin verifier or active sessions. Profile actions may still
contain literal password-entry text, so backups and the local `profiles/`
directory must be treated as sensitive. Restore validates archive paths and
schema before replacing configuration.

Never commit `profiles/*.json`, `profiles/assets/*`, `data/auth.json`, local
credential files, logs, or exported `.d6config` files. The repository's
`.gitignore` excludes these paths.

Before creating a commit or pull request, run
`python scripts/public_release_check.py`. It checks the tracked and
non-ignored working-tree surface for common credential formats and
computer-specific paths without printing matching values.

Before exposing the service beyond localhost, perform a separate threat-model
review and add explicit authorization boundaries for any remote users. This
project is designed for one local Windows user.
