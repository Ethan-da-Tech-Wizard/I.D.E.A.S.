# I.D.E.A.S. — NeonDAW

A local desktop creative-suite prototype with audio tooling, synthesized sample assets, a PySide6 interface, and development architecture.

**Status:** Creative application prototype  
**Built with:** Python, PySide6, NumPy, SciPy, sounddevice

[Project guide](docs/PROJECT_GUIDE.md) · [Every file explained](docs/FILE_GUIDE.md) · [Licenses and dependencies](docs/LICENSE_REVIEW.md)

## Start here

- [ARCHITECTURE.md](ARCHITECTURE.md) — Documentation: id_app Architecture. Covers Prohibited Technologies; Required Technologies; Rules.
- [THE_IDEAS_MANIFEST.md](THE_IDEAS_MANIFEST.md) — Reference notes for THE IDEAS MANIFEST: The application is "I.D.E.A.S." - a God-Tier, 5-in-1 creative software suite designed to be the ultimate and only application a creator needs. It combines a coding environment, music production, game development, artificial intelligence, an….
- [pyproject.toml](pyproject.toml) — Configuration or build metadata for pyproject; read alongside the application or tool that loads it.
- [scripts/launch-linux.sh](scripts/launch-linux.sh) — Command-line automation for launch linux; review prerequisites and side effects before executing.

[Detailed setup, usage, and development notes](PROJECT_NOTES.md) preserve the previous project documentation.

## Repository layout

| Path | Role |
| --- | --- |
| [.github/](.github/) | GitHub workflow and contributor configuration (1 tracked entries). |
| [app/](app/) | Application modules and user interface (708 tracked entries). |
| [docs/](docs/) | Project documentation, decisions, and reference material (23 tracked entries). |
| [scripts/](scripts/) | Build, launch, maintenance, and verification commands (15 tracked entries). |
| [tests/](tests/) | Automated checks and test fixtures (39 tracked entries). |

## Scope and current limits

See the platform scripts and package requirements before launching. Upstream Python and Qt packages retain their own licenses; generated samples and development instructions are documented separately.

## License

Original project code retains **GPL (see exact version in license)** terms in [LICENSE](LICENSE). Third-party libraries, media, data, models, and tools retain their own terms. See the [license review](docs/LICENSE_REVIEW.md) and [dependency inventory](docs/DEPENDENCIES.csv).
