# HTTP API

Base URL `http://localhost:<port>`. Every POST needs the header `X-Voice-Studio: 1`, and its `Origin` (if sent) must be this studio. Errors come back as `{"error": "..."}` with a 4xx/5xx status. Take files are named `full_t<N>.wav` (or `c<N>_t<N>.wav` for per-cue takes).

## Read

| Method | Path | Returns |
|---|---|---|
| GET | `/` | The app |
| GET | `/static/<file>` | App assets (js, css, svg) |
| GET | `/proxy.mp4` | The picture. Range requests supported |
| GET | `/api/info` | `version`, `capabilities` {ffmpeg, transcribe, clean}, `sets`, `current_set`, `preferred_mic`, `data_dir` |
| GET | `/api/state` | `takes` {cue: [{n, file, sec, offset, segs, peak, script, job, jobmsg, clean, cleanmsg, mtime}]}, `selected`, `autosave` (seconds), `roomtone`, `video` |
| GET | `/api/cues` | `{video, cues: [{t, label, kind}]}` for the current video |
| GET | `/api/notes` | `{text}`: my-script.txt |
| GET | `/api/video_status` | `{state: idle|working|done|error, msg, video}` |
| GET | `/api/transcribe_status?file=` | `{state: none|running|done|error, msg}` |
| GET | `/rec/<take>.wav`, `/rec/_roomtone.wav` | Audio |
| GET | `/rec/clean/<full take>.wav` | Cleaned audio |
| GET | `/script/<full take>.txt` | Transcript, as a download |

## Write

| Path | Body | Does |
|---|---|---|
| `/api/take?cue=&sec=&offset=&peak=&segs=` | WAV | Saves a new take and selects it. Full takes start transcription and cleanup |
| `/api/select?cue=&n=` | none | Chooses the take to use (404 if it does not exist) |
| `/api/delete?file=` | none | Moves a take to `_trash` (409 while a job still runs on it) |
| `/api/restore?file=` | none | Brings back the newest deleted copy as a new take |
| `/api/transcribe?file=` | none | Starts the transcript |
| `/api/clean?file=` | none | Starts or retries cleanup |
| `/api/script_save?file=` | text | Replaces a take's transcript text |
| `/api/notes` | text | Saves my-script.txt |
| `/api/cues` | JSON list | Saves flags (max 500; labels 80 characters) |
| `/api/cues_reset` | none | Restores flags from cues-seed.json |
| `/api/autosave?sr=&reset=1` | raw PCM s16le | Crash autosave. `reset=1` starts a new file and recovers any leftover first |
| `/api/autosave_recover` | none | Turns the autosave into a take |
| `/api/autosave_clear` | none | Drops the autosave |
| `/api/roomtone` | WAV | Saves room tone |
| `/api/setvideo?name=` | video file | Prepares a new picture in the background. Poll `/api/video_status` |
| `/api/export` | none | Writes `recordings/manifest.json`. Returns `{ok, path, file, clean}` |
| `/api/openfolder` | none | Opens the takes folder (Windows) and returns its path |
| `/api/sets/switch?name=` | none | Archives the takes and loads a project (409 while jobs run) |

## The slip timeline (`segs`)

Each take stores the events of its recording, in order:

```json
{"ev": "redo", "t": 12.41, "wav": 12.38, "vid": 9.02, "label": "optional"}
```

`t` is wall-clock seconds since Record, `wav` is the position in the audio file, and `vid` is the picture time. The events are `start`, `video_play`, `audio_pause`/`audio_resume` (no audio is written in between), `video_pause`/`video_resume`, `video_seek`, `redo` (the attempt before it is a slip), `flag` (a marked slip), `flag_add`, `hold`/`release` (held at a flag), and `stop`.
