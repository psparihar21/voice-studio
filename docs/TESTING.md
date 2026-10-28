# Testing

## Automated suite

```bash
python -m unittest discover -s tests -v
```

There are 28 tests in `tests/test_api.py`. Each one starts a real server on a free port with a throwaway data folder. Whisper and cleanup run through stand-in scripts. The video tests run only when ffmpeg is installed.

| Area | What is checked |
|---|---|
| Security | Foreign Host refused; POST without the app header refused; foreign Origin refused; no CORS preflight; CSP and hardening headers; path traversal on every file route; size caps before reading; negative lengths; truncated uploads; a smuggled second request inside a rejected body is never run; loopback-only config |
| Input handling | Non-numeric, NaN and negative values, bad cue names, non-WAV data, malformed flag JSON: all return 400 and create nothing |
| Takes | Upload, metadata, numeric ordering (t2 before t10), select, delete with selection fallback, restore with metadata, trash keeps every copy when names are reused, no delete while a job runs |
| Jobs | Transcript built from word timings (line breaks on pauses), edited and downloaded; cleanup failure is reported and can be retried |
| Media | Range requests (start, suffix, open-ended, 416), HEAD |
| Crash safety | Autosave append, odd-byte trim, recover to a take, clear; a new recording turns leftover audio into a take instead of overwriting it |
| Content | Room tone, notes with CRLF and Devanagari, flag save/clip/reset, export manifest |
| Robustness | A corrupt meta.json does not break the studio |
| Projects | Switch archives takes and loads the project, re-archiving never overwrites, unknown/traversal names refused, refused while busy, CLI switch |
| Video | A real ffmpeg conversion, name sanitising, keeps the previous proxy; a bad file reports an error and keeps the old picture |

## Code reviews

Two automated reviews ran on version 2.0.0 before release: a security review and a Python correctness review. All findings were fixed and covered by tests:

- **High.** A rejected POST left its body unread on a keep-alive connection, so a cross-site page could smuggle a second request past the guards. Fixed: every rejected POST closes the connection.
- Deleted takes with a reused name overwrote each other in the trash. Fixed with unique trash names.
- `os.replace` could fail while Windows held a file open. Fixed with retries.
- Video upload race and temp-file leak. The job is now claimed before the upload, and the temp file is removed on failure.
- A project switch could half-finish if the video was locked. The new files now load before anything is archived.
- Job threads could write results for a deleted take. Delete now waits for jobs.
- Job start was not atomic. It now runs under a lock.
- A new recording could overwrite unrecovered crash audio. That audio is now recovered as a take first.
- Truncated uploads were accepted. They are now refused.

## UAT (28 Oct 2026, Chrome, Windows 11, RTX 5070)

These checks were run against a copy of real project data, with a simulated 220 Hz microphone at −12 dBFS:

| # | Scenario | Result |
|---|---|---|
| 1 | App loads, project name, flags, takes with waveforms; no console errors | Pass |
| 2 | Enable microphone, live meter | Pass (−12 dBFS) |
| 3 | Test mic, then the ready card hides | Pass ("Mic is good") |
| 4 | Space records; M marks a slip; A pauses voice and picture (amber state, pause hint, scrub enabled); A resumes; R redoes 5 s; F adds a flag; Space stops | Pass. Take 13 saved, 0:09, level good |
| 5 | A new take runs real Whisper and DeepFilterNet in the background | Pass (script ready, voice cleaned) |
| 6 | Listen plays the take | Pass |
| 7 | My script panel: loads, autosaves while typing, saved on the server | Pass |
| 8 | Delete a take, then Undo | Pass (restored as Take 14) |
| 9 | Finish writes the manifest | Pass |
| 10 | Switch project while a job runs | Pass (refused, with a message) |
| 11 | Switch project when idle | Pass (new video, flags and script; old takes in recordings_archive/gta6in) |
| 12 | Phone width 375 px | Pass (no sideways scroll) |

Not covered by UAT: a physical microphone (a simulated one was used), the Change video button through the file picker (the server side is covered by the automated tests), and macOS or Linux.
