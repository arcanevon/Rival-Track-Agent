# Security Policy

## Supported version

Security fixes are applied to the latest version on the default branch.

## Reporting a vulnerability

Use GitHub private vulnerability reporting when it is enabled for the public repository. Do not include API keys, cookies, private evidence, or account credentials in a public issue.

Include the affected module, reproduction steps, expected impact, and a minimal redacted example. Public disclosure should wait until a fix or mitigation is available.

## Credential and data handling

- Store model and search credentials only in `.env` or another untracked secret store.
- Never commit browser profiles, login cookies, runtime logs, or generated evidence workspaces.
- Treat fetched webpages and user-supplied excerpts as untrusted input.
- Keep SSRF checks enabled for page-reading tools.
- Review demo evidence before redistribution; it is a reproducibility snapshot rather than a current factual claim.
