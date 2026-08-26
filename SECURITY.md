# Security policy

## Reporting

Please report suspected vulnerabilities privately through GitHub Security Advisories when the repository is published. Do not include live API keys, signed media URLs, private images, or provider request bodies in a public issue.

## Credential handling

- Configure only environment-variable names in Provider JSON.
- Never commit local `video-providers.json`, `.env` files, generated task reports, or raw private media.
- Rotate a credential immediately if it appears in a prompt, command line, report, CI log, issue, or commit.
- Review a custom `command` Adapter before enabling it; enabling one authorizes that executable to read the approved task paths and inherited environment.
- The route executor passes only a small runtime allowlist plus the selected Provider's declared credential variables to child processes; undeclared environment variables are not inherited.

## Trust boundaries

Provider discovery proves only that local dependencies and credential-variable names are present. It does not prove remote availability, quota, safety, or billing status. External execution is limited to one selected route attempt at a time and requires a hash-bound approved static revision.
