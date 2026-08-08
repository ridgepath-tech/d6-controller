# Security notes

The controller is intended to run locally on Windows and binds its HTTP
service to `127.0.0.1` only. It is not an internet-facing service.

Password-entry actions are stored in the local profile as text so Windows can
emit them through `SendInput`. Treat `profiles/default.json` and any local
credential source as sensitive. They are intentionally excluded from the
public repository. Do not commit credentials, generated personal artwork,
logs, or exported device files.

Before exposing the service beyond localhost, add authentication, authorization,
encrypted secret storage, and an explicit threat model. That is outside the
scope of this local controller.
