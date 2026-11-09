# Security policy

This repository contains build scripts, tests, documentation, and local patches.
It does not distribute game files, firmware, keys, or a runnable game.

## Reporting

Use GitHub's private vulnerability reporting / Security Advisories for this
repository if it is enabled. Do not open a public issue for a secret, key,
credential, or proprietary-file exposure.

Include:

- the affected file or workflow;
- the commit or run identifier;
- a minimal, sanitized reproduction;
- the impact you believe it has.

Do **not** attach an EBOOT/ELF, ISO, PKG, save, firmware, key, game asset,
capture, memory dump, or a log containing personal paths. A hash and a
sanitized excerpt are enough for triage.

## Scope

In scope:

- accidental publication of secrets or proprietary material;
- unsafe GitHub Actions permissions, untrusted input handling, or artifact
  uploads;
- command injection or path traversal in the public scripts;
- CI dependency or pinning problems.

Out of scope:

- piracy, decryption, or distribution of the game;
- vulnerabilities in the commercial game or in third-party tools themselves;
- reports that require a proprietary file and cannot be reduced to a sanitized
  technical description.

## Handling

Do not commit a fix for an exposed secret before rotating it. Remove the
material from the current tree, preserve evidence privately, and rotate any
credential. A history rewrite is a separate, destructive decision for the
maintainer.
