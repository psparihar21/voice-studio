"""HTTP API + the web app. Listens on loopback only and refuses anything that is not this app talking to itself:
wrong Host (DNS rebinding), a foreign Origin, or a POST without the app's header (cross-site requests)."""
import http.server
import json
import logging
import math
import mimetypes
import os
import re
import socketserver
import urllib.parse

from . import __version__, config, sets
from .jobs import Jobs
from .store import CUE, FULL, NAME, Store

log = logging.getLogger("voice_studio")
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC = re.compile(r"^/static/([\w-]+\.(?:js|css|svg|png|ico))$")
APP_HEADER = "X-Voice-Studio"
MB = 1024 * 1024
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")


class HttpError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def num(q, key, default=0.0):
    try:
        v = float((q.get(key) or [default])[0])
    except ValueError:
        raise HttpError(400, "%s must be a number" % key)
    if not math.isfinite(v):
        raise HttpError(400, "%s must be a number" % key)
    return v


def arg(q, key):
    return (q.get(key) or [""])[0]


class App:
    """Everything one running studio shares across requests."""

    def __init__(self, data_dir, cfg):
        self.cfg = cfg
        self.store = Store(data_dir)
        self.jobs = Jobs(self.store, cfg)
        self.caps = config.capabilities(cfg)
        self.port = cfg["port"]


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VoiceStudio"
    sys_version = ""
    app = None  # set by make_server

    def log_message(self, fmt, *a):
        log.debug("%s %s", self.address_string(), fmt % a)

    # ---------- responses ----------
    def _common_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")

    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self._common_headers()
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path, ctype=None, in_memory=False, download=None):
        if not os.path.isfile(path):
            return self.send_json({"error": "not found"}, 404)
        size = os.path.getsize(path)
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end, code = 0, size - 1, 200
        m = re.match(r"^bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        if m and size and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self._common_headers()
                self.end_headers()
                return
            code = 206
        length = end - start + 1 if size else 0
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % download)
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self._common_headers()
        self.end_headers()
        if self.command == "HEAD" or not length:
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                if in_memory:  # read fully and close BEFORE sending, so the file can be deleted any time
                    data = f.read(length)
                    f.close()
                    self.wfile.write(data)
                    return
                left = length
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    # ---------- request guards ----------
    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        names = ("127.0.0.1", "localhost", "[::1]")
        return host in names or host in ["%s:%d" % (n, self.app.port) for n in names]

    def _origin_ok(self):
        o = self.headers.get("Origin")
        if o is None:
            return True
        return o.lower() in ["http://%s:%d" % (n, self.app.port) for n in ("127.0.0.1", "localhost", "[::1]")]

    def length(self, limit):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise HttpError(400, "bad Content-Length")
        if n < 0:
            raise HttpError(400, "bad Content-Length")
        if n > limit:
            self.close_connection = True  # the body is not read, so the connection cannot be reused
            raise HttpError(413, "That is too large.")
        return n

    def body(self, limit=64 * 1024):
        n = self.length(limit)
        data = self.rfile.read(n) if n else b""
        if len(data) != n:
            raise HttpError(400, "The upload was cut off.")
        return data

    def _dispatch(self, method):
        try:
            if not self._host_ok():
                raise HttpError(403, "forbidden host")
            if method == "POST" and (self.headers.get(APP_HEADER) != "1" or not self._origin_ok()):
                raise HttpError(403, "forbidden")
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            (self.get if method in ("GET", "HEAD") else self.post)(u.path, q)
        except HttpError as e:
            if method == "POST":
                self.close_connection = True  # the body may be unread; reusing the socket would parse it as a request
            self.send_json({"error": e.msg}, e.code)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            self.close_connection = True
            log.exception("%s %s failed", method, self.path)
            try:
                self.send_json({"error": "Something went wrong in the studio. Details are in voice-studio.log."}, 500)
            except OSError:
                pass

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_OPTIONS(self):  # no CORS: other sites may not call the studio
        self.send_json({"error": "method not allowed"}, 405)

    # ---------- GET ----------
    def get(self, p, q):
        s, jobs = self.app.store, self.app.jobs
        if p in ("/", "/index.html"):
            return self.send_file(os.path.join(WEB, "index.html"), "text/html; charset=utf-8", True)
        if p == "/favicon.ico":
            return self.send_file(os.path.join(WEB, "icon.svg"), "image/svg+xml", True)
        m = STATIC.match(p)
        if m:
            return self.send_file(os.path.join(WEB, m.group(1)), None, True)
        if p == "/proxy.mp4":
            return self.send_file(s.proxy, "video/mp4")
        if p == "/api/info":
            c = self.app.cfg
            return self.send_json({"version": __version__, "capabilities": self.app.caps, "preferred_mic": c["preferred_mic"],
                                   "sets": sets.available(s), "current_set": sets.current(s), "data_dir": s.root})
        if p == "/api/cues":
            return self.send_json({"video": s.video_info().get("name"), "cues": s.cues()})
        if p == "/api/notes":
            return self.send_json({"text": s.notes()})
        if p == "/api/video_status":
            return self.send_json(dict(jobs.video, video=s.video_info()))
        if p == "/api/state":
            return self.send_json(self.state())
        m = re.match(r"^/script/(full_t\d+)\.txt$", p)
        if m:
            return self.send_file(s.txt_path(m.group(1) + ".wav"), "text/plain; charset=utf-8", True, m.group(1) + "_script.txt")
        if p == "/api/transcribe_status":
            return self.send_json(jobs.transcript_state(arg(q, "file")))
        m = re.match(r"^/rec/clean/(full_t\d+\.wav)$", p)
        if m:
            return self.send_file(s.clean_path(m.group(1)), "audio/wav", True)
        m = re.match(r"^/rec/((?:c\d+|full)_t\d+\.wav|_roomtone\.wav)$", p)
        if m:
            return self.send_file(s.wav(m.group(1)), "audio/wav", True)
        raise HttpError(404, "not found")

    def state(self):
        s, jobs = self.app.store, self.app.jobs
        m = s.load_meta()
        t = s.takes()
        for lst in t.values():
            for it in lst:
                fn = it["file"]
                it.update(sec=m["sec"].get(fn), offset=m["offset"].get(fn), segs=m["segs"].get(fn), peak=m["peak"].get(fn),
                          script=os.path.isfile(s.txt_path(fn)), job=jobs.transcripts.get(fn, {}).get("state"),
                          jobmsg=jobs.transcripts.get(fn, {}).get("msg"), clean=jobs.clean_state(fn),
                          cleanmsg=jobs.cleans.get(fn, {}).get("msg"))
                try:
                    it["mtime"] = os.path.getmtime(s.wav(fn))
                except OSError:
                    it["mtime"] = None
        return {"takes": t, "selected": m["selected"], "autosave": s.autosave_sec(),
                "roomtone": os.path.exists(s.roomtone), "video": s.video_info()}

    # ---------- POST ----------
    def post(self, p, q):
        s, jobs, cfg = self.app.store, self.app.jobs, self.app.cfg
        if p == "/api/setvideo":
            if not self.app.caps["ffmpeg"]:
                raise HttpError(501, "ffmpeg is not installed, so a new video cannot be prepared.")
            n = self.length(cfg["max_video_mb"] * MB)
            if n <= 0:
                raise HttpError(400, "That file is empty.")
            if not jobs.claim_video():
                raise HttpError(409, "Still preparing the last video.")
            tmp = os.path.join(s.root, "_incoming_video.tmp")
            started = False
            try:
                left = n
                with open(tmp, "wb") as f:
                    while left > 0:
                        chunk = self.rfile.read(min(MB, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                if left:
                    raise HttpError(400, "The upload was cut off.")
                name = re.sub(r"[\x00-\x1f]", "", os.path.basename(arg(q, "name") or "video"))[:120] or "video"
                jobs.start_video(tmp, name)
                started = True
            finally:
                if not started:
                    jobs.video.update(state="idle", msg="")
                    if os.path.isfile(tmp):
                        os.remove(tmp)
            return self.send_json({"state": "working"})

        if p == "/api/take":
            cue = arg(q, "cue")
            data = self.body(cfg["max_take_mb"] * MB)
            if not CUE.match(cue) or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
                raise HttpError(400, "bad take")
            try:
                segs = json.loads(arg(q, "segs") or "null")
            except ValueError:
                segs = None
            n, fn = s.add_take(cue, data, num(q, "sec"), num(q, "offset"), segs if isinstance(segs, list) else None, num(q, "peak"))
            if cue == "full":
                jobs.after_take(fn)  # script and cleaned voice are ready by the time the user asks
            return self.send_json({"n": n, "file": fn})

        if p == "/api/autosave":
            data = self.body(64 * MB)
            sr = int(num(q, "sr", 48000))
            if not 8000 <= sr <= 192000:
                raise HttpError(400, "bad sample rate")
            if arg(q, "reset") == "1" and s.autosave_sec() > 0:
                r = s.autosave_recover()  # crash audio nobody recovered: keep it as a take, never overwrite it
                if r:
                    jobs.after_take(r[1])
            s.autosave_write(data[: len(data) // 2 * 2], sr, arg(q, "reset") == "1")
            return self.send_json({"ok": True})

        if p == "/api/roomtone":
            data = self.body(64 * MB)
            if data[:4] != b"RIFF":
                raise HttpError(400, "bad audio")
            s.save_roomtone(data)
            return self.send_json({"ok": True})

        if p in ("/api/notes", "/api/script_save"):
            data = self.body(2 * MB).decode("utf-8", "replace")
            if p == "/api/notes":
                s.save_notes(data)
            else:
                fn = arg(q, "file")
                if not NAME.match(fn):
                    raise HttpError(400, "bad request")
                s.save_take_script(fn, data)
            return self.send_json({"ok": True})

        data = self.body(MB)
        if p == "/api/cues":
            try:
                lst = json.loads(data.decode("utf-8"))
                assert isinstance(lst, list) and len(lst) < 500
                lst = [{"t": round(float(c["t"]), 2), "label": str(c.get("label", ""))[:80], "kind": str(c.get("kind", "custom"))[:12]} for c in lst]
                assert all(math.isfinite(c["t"]) and c["t"] >= 0 for c in lst)
            except (ValueError, TypeError, KeyError, AssertionError, AttributeError):
                raise HttpError(400, "bad cues")
            s.save_cues(lst)
            return self.send_json({"ok": True})
        if p == "/api/cues_reset":
            if not s.reset_cues():
                raise HttpError(404, "no seed")
            return self.send_json({"ok": True})
        if p == "/api/select":
            cue = arg(q, "cue")
            try:
                n = int(arg(q, "n"))
            except ValueError:
                raise HttpError(400, "bad take number")
            if not CUE.match(cue) or n not in [t["n"] for t in s.takes().get(cue, [])]:
                raise HttpError(404, "no such take")
            s.select(cue, n)
            return self.send_json({"ok": True})
        if p == "/api/delete":
            fn = arg(q, "file")
            if not NAME.match(fn):
                raise HttpError(400, "bad file")
            if jobs.running(fn):
                raise HttpError(409, "This take is still being cleaned or transcribed. Try again in a moment.")
            try:
                s.delete(fn)
            except PermissionError:
                raise HttpError(409, "Windows is still using that file. Stop playback and try again.")
            jobs.forget(fn)
            return self.send_json({"ok": True})
        if p == "/api/restore":
            r = s.restore(arg(q, "file"))
            if not r:
                raise HttpError(404, "nothing to restore")
            if r[1].startswith("full_"):
                jobs.after_take(r[1])
            return self.send_json({"ok": True, "n": r[0], "file": r[1]})
        if p == "/api/transcribe":
            fn = arg(q, "file")
            if not NAME.match(fn) or not os.path.isfile(s.wav(fn)):
                raise HttpError(400, "bad file")
            if os.path.isfile(s.txt_path(fn)):
                return self.send_json({"state": "done"})
            jobs.start_transcribe(fn)
            return self.send_json({"state": "running"})
        if p == "/api/clean":
            fn = arg(q, "file")
            if not FULL.match(fn) or not os.path.isfile(s.wav(fn)):
                raise HttpError(400, "bad file")
            jobs.start_clean(fn)
            return self.send_json({"state": jobs.clean_state(fn)})
        if p == "/api/autosave_clear":
            s.autosave_clear()
            return self.send_json({"ok": True})
        if p == "/api/autosave_recover":
            r = s.autosave_recover()
            if not r:
                raise HttpError(404, "nothing to recover")
            jobs.after_take(r[1])
            return self.send_json({"n": r[0], "file": r[1]})
        if p == "/api/export":
            man, path = s.export()
            return self.send_json({"ok": bool(man["file"]), "path": path, "file": man["file"], "clean": man["clean_file"]})
        if p == "/api/openfolder":
            try:
                os.startfile(s.rec)  # Windows only; elsewhere the path is shown in the app
            except (AttributeError, OSError):
                pass
            return self.send_json({"ok": True, "path": s.rec})
        if p == "/api/sets/switch":
            if jobs.busy():
                raise HttpError(409, "Wait until the background work on your takes has finished.")
            try:
                prev = sets.switch(s, arg(q, "name"))
            except ValueError as e:
                raise HttpError(404, str(e))
            return self.send_json({"ok": True, "previous": prev, "current": arg(q, "name")})
        raise HttpError(404, "not found")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # Windows lets a second server steal the port otherwise


def make_server(data_dir, cfg):
    app = App(data_dir, cfg)
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = Server((cfg["host"], cfg["port"]), handler)
    app.port = srv.server_address[1]  # port 0 picks a free one (tests)
    return srv, app
