# Changelog

## 2.1.0 (2026-10-28)

### Added
- Resizable layout. Drag the bar under the video to make the picture taller or shorter; the controls below move with it. Drag the bar between the columns to change the side panel width. Both work with the arrow keys (Shift for bigger steps), reset on double-click or Home, and are remembered between sessions.
- `scripts/make_demo.py` builds a demo project with no third-party footage.
- `scripts/screenshots.mjs` regenerates every README image and the resize GIF through headless Chrome.

### Docs
- README rewritten as a user guide: install, a step-by-step walkthrough, shortcuts, recording tips, settings, troubleshooting. Five new images.

## 2.0.0 (2026-10-28)

This is a full rebuild as a product. Every version 1 feature is kept, and version 1 data folders work as they are.

### Added
- Project switcher in the header (replaces running `switch.ps1` by hand), plus `python -m voice_studio switch`.
- `config.json` for port, Whisper language and vocabulary, preferred mic and tool paths. No personal values are hard-coded any more.
- Capability detection: features whose tool is missing are hidden instead of failing.
- About line with the version and data folder (press `?`).
- Log file (`voice-studio.log`, rotating).
- A new icon (SVG, PNG and multi-size ICO) and a redesigned interface.
- Test suite (28 tests), and docs for the API, architecture and testing.

### Security
- Host allow-list (DNS rebinding), a required app header and Origin check on POST (CSRF), and the connection is closed after a rejected POST (request smuggling).
- Strict CSP: all scripts are moved out of the page, and the worklet is served as a file.
- `nosniff`, `X-Frame-Options`, `Referrer-Policy`, `Cross-Origin-Resource-Policy` headers.
- Size caps are checked before a body is read. Truncated uploads are refused, and bad numbers return 400 instead of crashing a request.
- The studio refuses to listen on anything but loopback.

### Fixed
- Atomic JSON and text writes, retried while Windows holds a file.
- The trash keeps every deleted take, even when a take name is reused.
- A restored take keeps its peak level.
- A new recording no longer overwrites crash audio that was not recovered.
- Delete waits for running jobs on that take. Job start is race-free. A video upload is claimed before it is read.
- Project switch loads the new files before archiving, and never overwrites an older archive.
- Notes keep their line endings.

## 1.x

This was the single-file studio (`server.py` and `index.html`).
