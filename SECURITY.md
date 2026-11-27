# Security Policy

## Supported Versions

Security updates are applied to the latest `main` branch and current `v0.1.x` releases.

## Reporting a Vulnerability

Please do not open public GitHub issues for suspected security vulnerabilities, authentication bypasses, or credential exposures.

Use GitHub Security Advisories ("Report a vulnerability" in the repository Security tab) to submit a private report with:

- A clear description of the issue and affected endpoint or module.
- Minimal reproduction steps or proof-of-concept request.
- Any suggested mitigation or patch.

## Security Practices

- Production deployments run with `ROLLMETRICS_ENVIRONMENT=cloud` and `ROLLMETRICS_ALLOW_LOCAL_DEV_BYPASS=false`, verifying Google OAuth2 ID tokens via `google.oauth2.id_token.verify_oauth2_token`.
- Secrets such as `GEMINI_API_KEY` are injected at runtime via environment variables or Cloud Run Secret Manager and are never stored in source control.
- CI enforces an automated PII and secret hygiene gate on every push and pull request.
