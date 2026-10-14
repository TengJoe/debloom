# Debloom 0.1.0 verification

Acceptance date: 2026-10-14. The machine-readable evidence is [verification.json](verification.json).

## Functional tests

75 tests passed:

| Area | Tests | Scope |
|---|---:|---|
| Appearance | 7 | persistence, private permissions, system notification, failed saves |
| GUI control | 6 | file arguments, picker fallback, isolated smoke, failure exit code, callback failures |
| Package backend | 21 | staging, metadata, architecture-qualified queries, plan/state/hash changes, result readback |
| APT transaction guard | 13 | isolated real APT with a dpkg stub, changed plan/hash/state rejection |
| Terminal compatibility | 14 | original files, staging permissions, error codes, cancellation, cleanup |
| Deployment | 5 | backup integrity, later edits, symlink rejection, archive/restore, unusual home paths |
| Localization | 4 | default, persistence, invalid language, save failure, live changes |
| Update check | 5 | newer/current/older versions, invalid/offline/oversized responses, fixed release URL, independent task state |

Changed deployment, localization, update and rendering modules also pass Ruff checks. The existing installation guard source retains its pre-redesign SHA-256.

## Native rendering and bridge

Real WebKitGTK smoke runs produced 14 screenshots each at 1040×800 and 800×640. The HTML review button triggered the native Python backend and real APT simulation. Every recorded frontend assertion passed, including literal external text, no HTML injection/password field, no-change/busy/error behavior, bounded logs and settings access during a busy package task.

All three appearance choices and both languages were exercised through actual select → native bridge → persistence → WebKit readback. Both complete language/theme combinations and the settings dialog were captured. All recorded appearance and locale checks report no horizontal overflow.

The published PNGs contain an inert demo package and simulated installation plan. No success or root authorization screenshot is presented as a real package installation.

The ten application runtime files match their deployed copies byte-for-byte. All nine PNG sizes in hicolor and DockNormalized match freshly rendered SVG pixels. Desktop entry validation passes.

## Review fixes

- Successful version readback now refreshes installed-version metadata. A failed readback leaves it unchanged.
- dpkg queries specify the requested architecture, avoiding multiarch ambiguity.
- A missing xdg-user-dir no longer prevents the file picker from opening.
- Dark step numbering uses the main button blue: white text contrast is approximately 5.17:1.
- Initial reduced-motion WebKit measurement reports no remaining transitions, animations or transforms.
- The SVG filter covers the top lid; the white arrow, continuous-curvature corners and UI/desktop artwork share one source.
- Original UI rollback rejects later target edits in isolated reproduction. The new deployment rollback checks all targets before restoring any file, verifies backups and archives new files.

Main/secondary/muted text, primary buttons and code combinations retain contrast above 4.5:1. This is a measured subset, not a certificate for every WCAG success criterion.

## Reproduce

```bash
/usr/bin/python3 -m unittest discover -v
/usr/bin/python3 scripts/capture_previews.py
/usr/bin/python3 scripts/render_cover.py
```

The capture script requires a desktop session. APT hook tests use isolated package state, cache and logs and substitute dpkg; they do not install test software on the real system.

## Practical limits

- Real administrator authorization and real DEB writes were not performed during this acceptance.
- A complete screen-reader workflow, all display scale factors and live GNOME Shell Dock caching were not independently accepted.
- Ubuntu 24.04 dependency availability is documented; local native acceptance used the desktop runtime versions recorded in the JSON. GitHub CI tests its own Ubuntu runner separately.
- Package maintenance scripts remain privileged third-party code; failed system installation is not automatically rolled back.
- Application rollback restores the recorded deployment state; repeated deployments should be restored newest first. It cannot guarantee a cross-file transaction against a power failure or simultaneous external writes.
