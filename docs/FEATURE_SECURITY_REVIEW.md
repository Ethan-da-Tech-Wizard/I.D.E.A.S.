# Feature Security Review

Copy this checklist into the implementation notes for every new pillar feature.
Do not merge the feature until every applicable item is answered.

## Feature

- Name:
- Pillar: IDE / DAW / Engines / AI / Studio
- Owner:
- New dependencies:
- New file types:
- New write locations:
- New long-running tasks:

## Threat Model Updates

- What user-controlled input enters this feature?
- What files can it read?
- What files can it write?
- Can it start processes, threads, sockets, web views, plugins, interpreters, or VMs?
- Can it allocate large memory buffers or decode untrusted media?
- What happens if the input is malformed, huge, recursive, or path-traversal shaped?

## Required Controls

- Validate all paths before use.
- Cap file sizes, project sizes, media duration, track/layer counts, and memory use.
- Keep generated/cache data under private app data.
- Keep network disabled unless explicitly approved in `docs/PRIVACY.md`.
- Keep arbitrary code/process execution disabled unless explicitly approved in
  `docs/THREAT_MODEL.md` and covered by OS sandboxing.
- Add negative tests for malicious input.
- Run `python scripts/security_audit.py`.
- Run `python -m pytest tests -q`.

## High-Risk Feature Gates

These require a dedicated threat-model section before implementation:

- Terminal or shell.
- Plugins or dynamic imports.
- Git/network/cloud.
- AI remote calls or prompt upload.
- Embedded browser/web view.
- Video/photo import of complex formats.
- Emulator/VM execution.
- Auto-update.

## Completed Reviews

### Local Asset Catalog Foundation

- Name: Local Asset Catalog Foundation
- Pillar: Studio
- Owner: Agent 2
- New dependencies: None
- New file types: `.wav`, `.png`, `.jpg`, `.jpeg`, `.obj`, `.py`, `.txt` metadata only
- New write locations: None
- New long-running tasks: Bounded recursive directory scan only

User-controlled input is the explicit catalog root path and filenames beneath it.
The feature reads directory entries and file sizes only, rejects non-directory
roots, validates relative paths, caps scan count, path depth, path length, and
per-file size, and skips unsupported or invalid files. It cannot write files,
start processes, create sockets, load plugins, execute scripts, or decode media.
