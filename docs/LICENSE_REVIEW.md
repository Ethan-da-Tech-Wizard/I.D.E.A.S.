# License review — I.D.E.A.S. — NeonDAW

Reviewed 2026-10-12. Original project code retains **GPL (see exact version in license)** terms in [LICENSE](../LICENSE).

The owner selected MIT for previously unlicensed original code. Existing licenses were preserved. This choice does not relicense copied code, dependencies, datasets, model weights, fonts, logos, or game assets.

## Preserved notices

- [LICENSE](../LICENSE)

## Dependency evidence

[DEPENDENCIES.csv](DEPENDENCIES.csv) lists 32 manifest and lockfile records. Repeated packages can appear under different manifests, versions, or development scopes. Registry metadata is evidence, not a replacement for the actual license text. For unpinned ranges, `metadata_version` is the version inspected and is not a claim about every install. Go and system/build tools can require notices beyond package-manager metadata.

| Component | Declared version | License evidence |
| --- | --- | --- |
| PyPI: PySide6 | >=6.8.0 | [LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only](https://pypi.org/pypi/PySide6/json) |
| PyPI: numpy | >=1.26 | [BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0](https://pypi.org/pypi/numpy/json) |
| PyPI: scipy | >=1.11 | [OSI Approved :: BSD License](https://pypi.org/pypi/scipy/json) |
| PyPI: sounddevice | >=0.4.6 | [MIT](https://pypi.org/pypi/sounddevice/json) |
| PyPI: setuptools | >=61.0 | [MIT](https://pypi.org/pypi/setuptools/json) |
| PyPI: pytest | unpinned | [MIT](https://pypi.org/pypi/pytest/json) |
| PyPI: altgraph | ==0.17.5 | [MIT](https://pypi.org/pypi/altgraph/0.17.5/json) |
| PyPI: iniconfig | ==2.3.0 | [MIT](https://pypi.org/pypi/iniconfig/2.3.0/json) |
| PyPI: packaging | ==26.2 | [Apache-2.0 OR BSD-2-Clause](https://pypi.org/pypi/packaging/26.2/json) |
| PyPI: pluggy | ==1.6.0 | [MIT](https://pypi.org/pypi/pluggy/1.6.0/json) |
| PyPI: Pygments | ==2.20.0 | [BSD-2-Clause](https://pypi.org/pypi/Pygments/2.20.0/json) |
| PyPI: PySide6 | ==6.11.1 | [LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only](https://pypi.org/pypi/PySide6/6.11.1/json) |
| PyPI: PySide6_Addons | ==6.11.1 | [LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only](https://pypi.org/pypi/PySide6_Addons/6.11.1/json) |
| PyPI: PySide6_Essentials | ==6.11.1 | [LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only](https://pypi.org/pypi/PySide6_Essentials/6.11.1/json) |
| PyPI: pyinstaller | ==6.17.0 | [GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs (including commercial ones)](https://pypi.org/pypi/pyinstaller/6.17.0/json) |
| PyPI: pyinstaller-hooks-contrib | ==2026.6 | [OSI Approved :: Apache Software License; OSI Approved :: GNU General Public License v2 (GPLv2)](https://pypi.org/pypi/pyinstaller-hooks-contrib/2026.6/json) |
| PyPI: pytest | ==9.1.1 | [MIT](https://pypi.org/pypi/pytest/9.1.1/json) |
| PyPI: setuptools | ==83.0.0 | [MIT](https://pypi.org/pypi/setuptools/83.0.0/json) |
| PyPI: shiboken6 | ==6.11.1 | [LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only](https://pypi.org/pypi/shiboken6/6.11.1/json) |
| PyPI: altgraph | ==0.17.5 \ | [MIT](https://pypi.org/pypi/altgraph/json) |
| PyPI: iniconfig | ==2.3.0 \ | [MIT](https://pypi.org/pypi/iniconfig/json) |
| PyPI: packaging | ==26.2 \ | [Apache-2.0 OR BSD-2-Clause](https://pypi.org/pypi/packaging/json) |
| PyPI: pluggy | ==1.6.0 \ | [MIT](https://pypi.org/pypi/pluggy/json) |
| PyPI: Pygments | ==2.20.0 \ | [BSD-2-Clause](https://pypi.org/pypi/Pygments/json) |
| PyPI: PySide6 | ==6.11.1 \ | [LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only](https://pypi.org/pypi/PySide6/json) |
| PyPI: PySide6_Addons | ==6.11.1 \ | [LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only](https://pypi.org/pypi/PySide6_Addons/json) |
| PyPI: PySide6_Essentials | ==6.11.1 \ | [LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only](https://pypi.org/pypi/PySide6_Essentials/json) |
| PyPI: pyinstaller | ==6.17.0 \ | [GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs (including commercial ones)](https://pypi.org/pypi/pyinstaller/json) |
| PyPI: pyinstaller-hooks-contrib | ==2026.6 \ | [OSI Approved :: Apache Software License; OSI Approved :: GNU General Public License v2 (GPLv2)](https://pypi.org/pypi/pyinstaller-hooks-contrib/json) |
| PyPI: pytest | ==9.1.1 \ | [MIT](https://pypi.org/pypi/pytest/json) |
| PyPI: setuptools | ==83.0.0 \ | [MIT](https://pypi.org/pypi/setuptools/json) |
| PyPI: shiboken6 | ==6.11.1 \ | [LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only](https://pypi.org/pypi/shiboken6/json) |

## Project-specific considerations

PySide6/Qt have separate LGPL/GPL/commercial terms; numerical and audio dependencies also retain their notices. The repository includes scripts/generate_synth_samples.py for generated audio assets. Any sample not covered by that provenance needs its own rights record before redistribution.

## Remaining work before binary distribution

No unresolved registry-license lookup remains in the listed package records. System packages, downloaded tools/models, optional plugins, release binaries, and externally obtained assets need a build-specific inventory. This source review is not a blanket legal certification or a claim that every release artifact has been audited.
