# Security

Riggle is a local desktop tool. The HTTP server binds `127.0.0.1`, mints a random
token at startup and requires it for API and media calls. It is not designed to
be exposed to a network or the internet.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

Use **GitHub private vulnerability reporting** on this repository
(Security → Advisories → Report a vulnerability). Please do not open a public
issue for an unfixed vulnerability.

Useful details: Riggle version, OS, Blender version, the affected route or file,
and a minimal reproduction. Do not include private models, tokens or personal
paths in a public report.
