# Vendored code

Files here are third-party code copied in rather than loaded from a CDN, so the page
works from a folder, with Live Server, and under `node site/app.js` with no network.

## Fuse.js

- **File:** `fuse.min.js`, unmodified, as published.
- **Version:** 7.0.0.
- **Source:** <https://unpkg.com/fuse.js@7.0.0/dist/fuse.min.js>.
- **Licence:** Apache-2.0, full text in `LICENSE-fuse.txt`; © Kiro Risk.
- **Used by:** the address field's suggestions in `../app.js` (`FUSE_OPTIONS`,
  `loadNames`, `suggestionsFor`). It searches the street names the page holds while a
  name is being typed; the lookup itself never uses it, and the page works without it
  (the list is then scanned directly).

To replace it, download the same file at the new version, update the version and the
source line above, and run `node site/app.js`: the checks build a search from this file
and would fail if it did not load.
