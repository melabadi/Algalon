# Security

## Supported versions

Use the latest stable release. Older releases are not maintained with security backports.

## Report a vulnerability

Use [private vulnerability reporting](https://github.com/melabadi/Algalon/security/advisories/new). Include affected versions, impact, and a minimal reproduction using synthetic data. Do not post credentials, prompt contents, raw telemetry, or personal data in public issues.

## Release checks

Release publication requires CodeQL analysis for JavaScript/TypeScript and Python, full-history secret scanning, npm and Python dependency audits, coverage gates, and smoke tests of the exact artifacts. External workflow actions are pinned to commit hashes. Weekly scans and Dependabot updates check for newly disclosed issues.

The release provides ZIP and self-installing PYZ assets with SHA-256 checksums and GitHub build-provenance attestations. New published releases are immutable. Verify provenance with `gh attestation verify <downloaded-file> --repo melabadi/Algalon`, and verify the corresponding checksum before installation.

Automated checks reduce risk; they do not prove the absence of vulnerabilities. Review dependency and code-scanning alerts before accepting a release.

## Local data boundary

Algalon is a loopback-only local application, not an Internet-facing service. Do not expose its ports through a public proxy. Prompt text and telemetry can contain sensitive information: keep local configuration, installation backups, Docker volumes, and CSV exports private. The public website uses synthetic screenshots and bundled example assumptions, never a live API connection.