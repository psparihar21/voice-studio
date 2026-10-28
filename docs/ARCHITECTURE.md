# Architecture

```
browser (voice_studio/web)                     python (voice_studio)
  index.html, app.css                            __main__.py   CLI: serve | switch
  app.js      UI, recording, flags, script  ──►  server.py     HTTP, request guards, routing
  worklet.js  mic samples → 16-bit PCM           store.py      takes, meta.json, flags, notes, autosave, trash
                                                 jobs.py       Whisper, DeepFilterNet, ffmpeg in threads
                                                 sets.py       projects: load and archive
                                                 config.py     defaults + <data>/config.json, capabilities
```

- **Recording happens in the browser.** An AudioWorklet copies mic blocks to the page, which keeps them as Int16 chunks. Every 5 s the new chunks are POSTed to `/api/autosave`. On Stop the page builds a WAV and POSTs it to `/api/take` with the slip timeline. The server never touches the microphone.
- **One App object per server** holds the Store, the Jobs and the capabilities. The handler class is bound to it, so tests can run several isolated studios in one process.
- **Disk is the source of truth.** Takes are files; `meta.json` holds what a WAV cannot. Reads tolerate a broken `meta.json`. Writes go to a temp file followed by `os.replace`, retried while Windows holds a file open.
- **Background jobs** run in threads. Whisper jobs queue on one lock (one GPU), and cleanup jobs on another. A job state lives in memory; a finished result is a file, so a restart still shows it.
- **Guards** run before routing: Host allow-list, then app header and Origin on POST. A rejected POST closes the connection.
- **Layout state** (video height, panel width, script panel position, toggles) is per-browser and kept in `localStorage`. Nothing about the layout is stored on the server.
- **Docs images** come from `scripts/make_demo.py` and `scripts/screenshots.mjs`, so they can be rebuilt after any UI change.
- **No dependencies** on the server side. The optional tools are external programs, found through config.
