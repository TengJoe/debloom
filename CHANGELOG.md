# Changelog

## 0.1.1

- Resolve empty and relative XDG config/data/state variables to their standard user defaults.
- Fix desktop entry quoting with GLib KeyFile and a fixed Python executable; paths containing dollar signs, backticks, percent signs, quotes, backslashes, spaces and Unicode now launch correctly.
- Test real GIO startup and exact file-argument forwarding, rather than only generated strings.
- Add isolated install checks that ensure invalid XDG variables never write into the working directory.
- Make the settings version follow the native application version without a stale HTML fallback.
- Strengthen host-independent appearance notification assertions.
- Separate parent and child CPU in benchmarks, measure combined imports directly, use consistent interpreter flags, preserve unique evidence, and remove unmeasured rewrite-speed claims.

## 0.1.0

- Publish Debloom as a per-user native local DEB installer.
- Add Chinese and English UI with saved language preferences.
- Consolidate appearance, language and version into accessible built-in settings.
- Add an explicit public GitHub Release update check and release-page link.
- Fix installed-version metadata after successful readback and qualify dpkg queries by architecture.
- Keep file selection usable when xdg-user-dir is unavailable.
- Fix dark step-number contrast, incomplete reduced-motion handling, SVG filter clipping and inconsistent UI/desktop logos.
- Add portable user-local deployment, backup and rollback with later-edit protection.
- Include actual desktop screenshots, a rendered introduction and isolated functional tests.
