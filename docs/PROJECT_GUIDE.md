# I.D.E.A.S. — NeonDAW

A local desktop creative-suite prototype with audio tooling, synthesized sample assets, a PySide6 interface, and development architecture.

**Status:** Creative application prototype  
**Built with:** Python, PySide6, NumPy, SciPy, sounddevice

## Reading order

1. Read the root README for project purpose and setup navigation.
2. Follow the entry points below to find the implementation.
3. Use the file guide to locate individual modules, tests, assets, and configuration.
4. Check component licenses before redistributing dependencies or assets.

## Important paths

- [ARCHITECTURE.md](../ARCHITECTURE.md) — Documentation: id_app Architecture. Covers Prohibited Technologies; Required Technologies; Rules.
- [THE_IDEAS_MANIFEST.md](../THE_IDEAS_MANIFEST.md) — Reference notes for THE IDEAS MANIFEST: The application is "I.D.E.A.S." - a God-Tier, 5-in-1 creative software suite designed to be the ultimate and only application a creator needs. It combines a coding environment, music production, game development, artificial intelligence, an….
- [pyproject.toml](../pyproject.toml) — Configuration or build metadata for pyproject; read alongside the application or tool that loads it.
- [scripts/launch-linux.sh](../scripts/launch-linux.sh) — Command-line automation for launch linux; review prerequisites and side effects before executing.

## Structure

| Path | Role |
| --- | --- |
| [.github/](../.github/) | GitHub workflow and contributor configuration (1 tracked entries). |
| [app/](../app/) | Application modules and user interface (708 tracked entries). |
| [docs/](../docs/) | Project documentation, decisions, and reference material (23 tracked entries). |
| [scripts/](../scripts/) | Build, launch, maintenance, and verification commands (15 tracked entries). |
| [tests/](../tests/) | Automated checks and test fixtures (39 tracked entries). |

## Scope

See the platform scripts and package requirements before launching. Upstream Python and Qt packages retain their own licenses; generated samples and development instructions are documented separately.

## Maintaining the guide

The file catalog is a source-inspection snapshot, not proof that every runtime path works. Update it when moving files or changing module responsibilities. The 2026-10-12 documentation review did not run every application, deploy services, or validate all historical performance claims.
