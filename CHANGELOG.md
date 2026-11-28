# Changelog

Changes to Trust Centre from the initial commit onward, grouped by the release that first included them.

## Unreleased

- Added this changelog and linked it from the README.

## [v1.0](https://github.com/roelsroels/trust-centre/releases/tag/v1.0) · 2026-11-28

- Added owner-configured OpenID Connect sign-in. Users link existing accounts, and administrators still complete portal MFA. [f4aff7f](https://github.com/roelsroels/trust-centre/commit/f4aff7f)
- Added the Public tier for documents, customer accounts, and CSV imports. Signed-out visitors can open published Public PDFs and accept any required agreement in their browser session. [91eef52](https://github.com/roelsroels/trust-centre/commit/91eef52)
- Added a branded social sharing card to the repository. [5294a58](https://github.com/roelsroels/trust-centre/commit/5294a58)
- Reworked the README into a fresh-install guide and removed legacy migration instructions. [bb2231c](https://github.com/roelsroels/trust-centre/commit/bb2231c)
- Linked to the GitHub repository from the shared site footer. [227a63b](https://github.com/roelsroels/trust-centre/commit/227a63b)

## [v0.8](https://github.com/roelsroels/trust-centre/releases/tag/v0.8) · 2026-11-27

### Portal and administration

- Built the initial self-hosted portal with a public homepage, tiered document access, private PDFs, version history, agreements, account management, access requests, and subscriber notifications. [11de724](https://github.com/roelsroels/trust-centre/commit/11de724)
- Added the opt-in Alder Quay Systems demo with six fictional PDFs and three compliance team profiles with AI-generated portraits. [0abb4b2](https://github.com/roelsroels/trust-centre/commit/0abb4b2)
- Replaced Django with Flask and integrated the Manage interface into the main site, with compatibility for existing records, passwords, and authenticators. [be81d70](https://github.com/roelsroels/trust-centre/commit/be81d70)
- Unified customer and administrator sign-in. Administrators verify their authenticator during sign-in and can then open Manage directly. [a6c3f74](https://github.com/roelsroels/trust-centre/commit/a6c3f74)
- Applied the Burgundy, Breeze, and Coral palette. [6a2adfe](https://github.com/roelsroels/trust-centre/commit/6a2adfe)
- Set Chromatica as the default typeface and added support for configurable webfonts. [50ff334](https://github.com/roelsroels/trust-centre/commit/50ff334)
- Added customer account requests with a company name, CSV import, and approval-triggered password setup emails. [d95af40](https://github.com/roelsroels/trust-centre/commit/d95af40)

### Email

- Added Date and unique Message-ID headers to outgoing messages. [734ca32](https://github.com/roelsroels/trust-centre/commit/734ca32)
- Validated SMTP envelopes and added standards-based MIME encoding, SMTP line endings, and support for international mailbox addresses when the relay supports SMTPUTF8. [7c3d567](https://github.com/roelsroels/trust-centre/commit/7c3d567)
- Added branded HTML email with a plain-text alternative and the configured logo when available. [641d12d](https://github.com/roelsroels/trust-centre/commit/641d12d)
- Made outgoing email use the website’s active colours and font settings. [e23eaff](https://github.com/roelsroels/trust-centre/commit/e23eaff)

### Deployment and repository

- Added `update.sh` to pull changes, rebuild the Docker image, and update the Compose services. [de3189b](https://github.com/roelsroels/trust-centre/commit/de3189b)
- Documented the Compose and SMTP setup and Manage workflows. [aacf4e7](https://github.com/roelsroels/trust-centre/commit/aacf4e7)
- Licensed the project under MIT. [3b278d6](https://github.com/roelsroels/trust-centre/commit/3b278d6)
- Added homepage, library, and Manage screenshots from the fictional demo to the README. [35c07ed](https://github.com/roelsroels/trust-centre/commit/35c07ed)
- Made application files readable by the unprivileged container user when the checkout has restrictive permissions. [9fc083b](https://github.com/roelsroels/trust-centre/commit/9fc083b)
- Added `trust-portal` to the Docker container names while retaining the existing Compose project, volume, and network. [0595b0f](https://github.com/roelsroels/trust-centre/commit/0595b0f)
- Added GitHub funding configuration. [4867b50](https://github.com/roelsroels/trust-centre/commit/4867b50)
- Updated the GitHub issue templates. [ffd6eab](https://github.com/roelsroels/trust-centre/commit/ffd6eab)
- Clarified the README project title. [3159227](https://github.com/roelsroels/trust-centre/commit/3159227)
