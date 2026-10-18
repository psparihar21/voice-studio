# Voice Studio

A small local web app for recording a voice-over against your video. It runs only on your computer (127.0.0.1); nothing is uploaded.

## Run
Needs Python 3.9+ and Chrome. Double-click `start.bat` (or `python server.py 8765`, then open http://localhost:8765). Pick your video in the app; it makes a silent 1280x720 preview with ffmpeg (ffmpeg must be on PATH).

## What it does
- Records the whole voice-over as one WAV (browser microphone, AudioWorklet), takes numbered `full_t1.wav`, `full_t2.wav`.
- Flags (cues) on the video timeline with a 3-2-1 countdown before the next part; add, jump and hold at flags.
- Pause the video and keep recording (or pause both), redo the last 5 s, mark a slip.
- Logs video seek / pause / redo events next to the recording so an editor can find the best attempt of each part.
- Movable script panel, script download per take, delete to a `_trash` folder (restorable).
- Optional auto-transcription after each take (see below).

## Shortcuts
Space record/stop, A pause everything, V pause video, R redo 5 s, M mark slip, F add flag, N/P next/previous flag, H hold at flags, B back 5 s, K play/pause, Left/Right skip, Enter release hold, ? help.

## Optional transcription
Set `VOICE_STUDIO_PY` to a Python that has `faster-whisper`, `VOICE_STUDIO_SCRIPTS` to a folder with `whisper_gpu.py` and `romanize.py`, and optionally `VOICE_STUDIO_PROMPT` to product names. Without them the app works; only the transcribe button is off.

## Your data
`recordings/`, `script.json`, `my-script.txt`, `cues.json`, `video.json` and the proxy videos are created next to `server.py` and are git-ignored.
