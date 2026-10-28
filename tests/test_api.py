"""End-to-end tests of the HTTP API against a real server on a free port, with a throwaway data folder.
Transcription and cleanup run through tiny stand-in scripts, so no GPU is needed. Run: python -m unittest -v"""
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from voice_studio import __version__, config, sets  # noqa: E402
from voice_studio.server import make_server  # noqa: E402
from voice_studio.store import Store  # noqa: E402

FAKE_WHISPER = r'''
import json, os, sys
out = sys.argv[2]
words = [{"text": "नमस्ते", "start": 0.1, "end": 0.5}, {"text": "दोस्तों", "start": 0.6, "end": 1.0}, {"text": "आज", "start": 2.5, "end": 2.8}]
json.dump(words, open(os.path.join(out, "transcript.json"), "w", encoding="utf-8"), ensure_ascii=False)
'''
FAKE_ROMANIZE = r'''
import json, os, sys
words = json.load(open(sys.argv[2], encoding="utf-8"))
rom = [dict(w, text=t) for w, t in zip(words, ["namaste", "doston", "aaj"])]
json.dump(rom, open(os.path.join(sys.argv[3], "transcript.roman.json"), "w", encoding="utf-8"))
'''
FAKE_ENHANCE = "import shutil, sys; shutil.copyfile(sys.argv[1], sys.argv[2])"


def wav_bytes(sec=1.0, sr=48000):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x10\x00" * int(sec * sr))
    return b.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vs-test-")
        self.data = os.path.join(self.tmp, "data")
        tools = os.path.join(self.tmp, "tools")
        os.makedirs(tools)
        os.makedirs(self.data)
        for name, src in (("whisper_gpu.py", FAKE_WHISPER), ("romanize.py", FAKE_ROMANIZE), ("enhance.py", FAKE_ENHANCE)):
            with open(os.path.join(tools, name), "w", encoding="utf-8") as f:
                f.write(src)
        with open(os.path.join(self.data, "proxy.mp4"), "wb") as f:
            f.write(bytes(range(256)) * 40)  # 10240 bytes stand in for a video
        json.dump({"name": "clip.mp4", "duration": 10.0}, open(os.path.join(self.data, "video.json"), "w"))
        json.dump({"clip.mp4": [{"t": 0, "label": "Intro", "kind": "section"}, {"t": 4.5, "label": "Part 2", "kind": "section"}]},
                  open(os.path.join(self.data, "cues-seed.json"), "w"))
        cfg = dict(config.DEFAULTS, port=0, whisper_python=sys.executable, whisper_scripts=tools,
                   enhance_python=sys.executable, enhance_script=os.path.join(tools, "enhance.py"))
        self.srv, self.app = make_server(self.data, cfg)
        self.port = self.app.port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for _ in range(20):  # background jobs may still hold files for a moment on Windows
            try:
                shutil.rmtree(self.tmp)
                break
            except OSError:
                time.sleep(0.2)

    def req(self, method, path, body=None, headers=None, app_header=True):
        h = {"Host": "localhost:%d" % self.port}
        if method == "POST" and app_header:
            h["X-Voice-Studio"] = "1"
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, body=b"", **kw):
        return self.req("POST", path, body, **kw)

    def js(self, resp):
        return json.loads(resp[1].decode("utf-8"))

    def add_take(self, sec=1.0, cue="full"):
        segs = json.dumps([{"ev": "start", "t": 0, "wav": 0, "vid": 0}])
        r = self.post("/api/take?cue=%s&sec=%s&offset=0.25&peak=0.5&segs=%s" % (cue, sec, segs.replace(" ", "")), wav_bytes(sec))
        self.assertEqual(r[0].status, 200, r[1])
        return self.js(r)

    def wait_jobs(self, timeout=30):
        end = time.time() + timeout
        while self.app.jobs.busy() and time.time() < end:
            time.sleep(0.1)
        self.assertFalse(self.app.jobs.busy(), "background jobs did not finish")


class SecurityTests(Base):
    def test_foreign_host_is_refused(self):
        r, _ = self.get("/api/state", headers={"Host": "evil.example:%d" % self.port})
        self.assertEqual(r.status, 403)

    def test_post_without_app_header_is_refused(self):
        r, _ = self.post("/api/notes", b"hacked", app_header=False)
        self.assertEqual(r.status, 403)
        self.assertEqual(self.js(self.get("/api/notes"))["text"], "")

    def test_post_from_other_origin_is_refused(self):
        r, _ = self.post("/api/notes", b"x", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)
        r, _ = self.post("/api/notes", b"x", headers={"Origin": "http://localhost:%d" % self.port})
        self.assertEqual(r.status, 200)

    def test_no_cors_preflight(self):
        r, _ = self.req("OPTIONS", "/api/notes", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 405)
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

    def test_security_headers(self):
        r, body = self.get("/")
        self.assertEqual(r.status, 200)
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertIn(b"/static/app.js", body)
        r, _ = self.get("/api/state")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(r.getheader("Cross-Origin-Resource-Policy"), "same-origin")

    def test_path_traversal_is_not_served(self):
        for p in ("/static/../server.py", "/static/..%2fserver.py", "/rec/..%2fmeta.json", "/rec/meta.json",
                  "/script/../../config.txt", "/rec/clean/..%2f..%2fproxy.mp4", "/static/app.py"):
            self.assertEqual(self.get(p)[0].status, 404, p)

    def test_size_limits_and_bad_lengths(self):
        # the server refuses on the declared length, before reading any of the body
        r, _ = self.post("/api/notes", b"x", headers={"Content-Length": str(2 * 1024 * 1024 + 1)})
        self.assertEqual(r.status, 413)
        r, _ = self.post("/api/take?cue=full", b"x", headers={"Content-Length": str(3 * 1024 ** 3)})
        self.assertEqual(r.status, 413)
        r, _ = self.post("/api/cues", b"[]", headers={"Content-Length": "-5"})
        self.assertEqual(r.status, 400)

    def test_bad_input_gets_400_not_a_crash(self):
        cases = [("/api/take?cue=full&sec=abc", wav_bytes()), ("/api/take?cue=full&sec=nan", wav_bytes()),
                 ("/api/take?cue=../x", wav_bytes()), ("/api/take?cue=full", b"NOTAWAV"),
                 ("/api/select?cue=full&n=x", b""), ("/api/autosave?sr=abc", b"\x00\x00"), ("/api/autosave?sr=5", b"\x00\x00"),
                 ("/api/cues", b"{not json"), ("/api/cues", b'[{"t":"NaN"}]'), ("/api/cues", b'[{"label":"no time"}]'),
                 ("/api/cues", b'[{"t":-1}]'), ("/api/delete?file=../../x.wav", b""), ("/api/script_save?file=x.txt", b"")]
        for path, body in cases:
            r, data = self.post(path, body)
            self.assertEqual(r.status, 400, (path, data))
        self.assertEqual(self.app.store.takes(), {})

    def test_rejected_body_cannot_smuggle_a_second_request(self):
        import socket
        inner = ("POST /api/notes HTTP/1.1\r\nHost: localhost:%d\r\nX-Voice-Studio: 1\r\nContent-Length: 6\r\n\r\nhacked" % self.port).encode()
        outer = ("POST /api/notes HTTP/1.1\r\nHost: localhost:%d\r\nOrigin: https://evil.example\r\nContent-Type: text/plain\r\n"
                 "Content-Length: %d\r\n\r\n" % (self.port, len(inner))).encode() + inner
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(outer)
        got = b""
        try:
            while True:
                b = s.recv(65536)
                if not b:
                    break
                got += b
        except socket.timeout:
            pass
        s.close()
        self.assertEqual(got.count(b"HTTP/1.1 "), 1, got)
        self.assertIn(b"403", got.split(b"\r\n")[0])
        self.assertEqual(self.js(self.get("/api/notes"))["text"], "")

    def test_truncated_upload_is_refused(self):
        import socket
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(("POST /api/take?cue=full HTTP/1.1\r\nHost: localhost:%d\r\nX-Voice-Studio: 1\r\nContent-Length: 5000\r\n\r\n" % self.port).encode() + wav_bytes(0.01)[:100])
        s.shutdown(socket.SHUT_WR)
        reply = s.recv(4096)
        s.close()
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])
        self.assertEqual(self.app.store.takes(), {})

    def test_server_only_binds_loopback(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        with self.assertRaises(SystemExit):
            os.makedirs(os.path.join(self.tmp, "c"))
            json.dump({"host": "0.0.0.0"}, open(os.path.join(self.tmp, "c", "config.json"), "w"))
            config.load(os.path.join(self.tmp, "c"))


class TakeTests(Base):
    def test_static_and_info(self):
        for p, ctype in (("/static/app.js", "javascript"), ("/static/app.css", "css"), ("/static/icon.svg", "svg"),
                         ("/static/worklet.js", "javascript"), ("/favicon.ico", "svg")):
            r, _ = self.get(p)
            self.assertEqual(r.status, 200, p)
            self.assertIn(ctype, r.getheader("Content-Type"), p)
        info = self.js(self.get("/api/info"))
        self.assertEqual(info["version"], __version__)
        self.assertTrue(info["capabilities"]["transcribe"])
        self.assertTrue(info["capabilities"]["clean"])

    def test_record_select_delete_restore(self):
        t1 = self.add_take(1.0)
        self.assertEqual(t1, {"n": 1, "file": "full_t1.wav"})
        self.wait_jobs()
        st = self.js(self.get("/api/state"))
        k = st["takes"]["full"][0]
        self.assertEqual((k["sec"], k["offset"], k["peak"], st["selected"]["full"]), (1.0, 0.25, 0.5, 1))
        self.assertEqual(k["segs"][0]["ev"], "start")
        self.assertTrue(k["script"])
        self.assertEqual(k["clean"], "done")

        t2 = self.add_take(2.0)
        self.assertEqual(t2["n"], 2)
        self.assertEqual(self.js(self.get("/api/state"))["selected"]["full"], 2)
        self.assertEqual(self.post("/api/select?cue=full&n=1")[0].status, 200)
        self.assertEqual(self.js(self.get("/api/state"))["selected"]["full"], 1)
        self.assertEqual(self.post("/api/select?cue=full&n=99")[0].status, 404)

        self.wait_jobs()
        self.assertEqual(self.post("/api/delete?file=full_t1.wav")[0].status, 200)
        st = self.js(self.get("/api/state"))
        self.assertEqual([t["n"] for t in st["takes"]["full"]], [2])
        self.assertEqual(st["selected"]["full"], 2)  # the selection moves to a take that still exists
        self.assertTrue(any(f.startswith("full_t1__") and f.endswith(".wav") for f in os.listdir(os.path.join(self.data, "recordings", "_trash"))))

        r = self.js(self.post("/api/restore?file=full_t1.wav"))
        self.assertEqual((r["n"], r["file"]), (3, "full_t3.wav"))
        self.wait_jobs()
        st = self.js(self.get("/api/state"))
        k3 = [t for t in st["takes"]["full"] if t["n"] == 3][0]
        self.assertEqual((k3["sec"], k3["offset"], k3["peak"]), (1.0, 0.25, 0.5))  # metadata came back too
        self.assertEqual(self.post("/api/restore?file=full_t1.wav")[0].status, 404)

    def test_trash_keeps_every_deleted_take(self):
        self.add_take(1.0, cue="c1")
        self.post("/api/delete?file=c1_t1.wav")
        self.add_take(2.0, cue="c1")  # the name c1_t1 is free again
        self.post("/api/delete?file=c1_t1.wav")
        trash = os.listdir(os.path.join(self.data, "recordings", "_trash"))
        self.assertEqual(len([f for f in trash if f.endswith(".wav")]), 2, trash)
        r = self.js(self.post("/api/restore?file=c1_t1.wav"))
        self.assertEqual(self.app.store.load_meta()["sec"][r["file"]], 2.0)  # the newest deletion comes back first

    def test_delete_waits_for_running_jobs(self):
        self.add_take(1.0, cue="c2")
        self.app.jobs.cleans["c2_t1.wav"] = {"state": "running", "msg": ""}
        self.assertEqual(self.post("/api/delete?file=c2_t1.wav")[0].status, 409)
        self.app.jobs.cleans.pop("c2_t1.wav")
        self.assertEqual(self.post("/api/delete?file=c2_t1.wav")[0].status, 200)

    def test_new_recording_keeps_unrecovered_crash_audio(self):
        self.post("/api/autosave?sr=48000&reset=1", b"\x01\x00" * 48000)
        self.post("/api/autosave?sr=48000&reset=1", b"\x02\x00" * 24000)  # next recording starts without recovering
        self.assertEqual([t["file"] for t in self.app.store.takes()["full"]], ["full_t1.wav"])
        self.assertAlmostEqual(self.app.store.autosave_sec(), 0.5)
        self.wait_jobs()

    def test_take_numbers_sort_numerically(self):
        for _ in range(11):
            self.add_take(0.1, cue="c1")
        nums = [t["n"] for t in self.app.store.takes()["c1"]]
        self.assertEqual(nums, list(range(1, 12)))

    def test_transcript_text_and_download(self):
        self.add_take(1.0)
        self.wait_jobs()
        self.assertEqual(self.js(self.get("/api/transcribe_status?file=full_t1.wav"))["state"], "done")
        r, body = self.get("/script/full_t1.txt")
        self.assertEqual(r.status, 200)
        self.assertIn("attachment", r.getheader("Content-Disposition"))
        text = body.decode("utf-8")
        self.assertIn("[0:00.1] namaste doston", text)
        self.assertIn("[0:02.5] aaj", text)  # a pause over 0.7 s starts a new line
        self.assertIn("नमस्ते", text)
        self.assertEqual(self.post("/api/script_save?file=full_t1.wav", "edited ✓".encode("utf-8"))[0].status, 200)
        self.assertEqual(self.get("/script/full_t1.txt")[1].decode("utf-8"), "edited ✓")
        self.assertEqual(self.js(self.post("/api/transcribe?file=full_t1.wav"))["state"], "done")

    def test_failed_job_reports_error_and_can_retry(self):
        self.app.cfg["enhance_script"] = os.path.join(self.tmp, "missing.py")
        self.add_take(1.0)
        self.wait_jobs()
        k = self.js(self.get("/api/state"))["takes"]["full"][0]
        self.assertEqual(k["clean"], "error")
        self.assertIn("not set up", k["cleanmsg"])
        self.app.cfg["enhance_script"] = os.path.join(self.tmp, "tools", "enhance.py")
        self.post("/api/clean?file=full_t1.wav")
        self.wait_jobs()
        self.assertEqual(self.js(self.get("/api/state"))["takes"]["full"][0]["clean"], "done")
        self.assertEqual(self.get("/rec/clean/full_t1.wav")[0].status, 200)

    def test_range_requests(self):
        whole = self.get("/proxy.mp4")[1]
        r, part = self.get("/proxy.mp4", headers={"Range": "bytes=100-199"})
        self.assertEqual((r.status, part), (206, whole[100:200]))
        self.assertEqual(r.getheader("Content-Range"), "bytes 100-199/%d" % len(whole))
        r, part = self.get("/proxy.mp4", headers={"Range": "bytes=-50"})
        self.assertEqual(part, whole[-50:])
        r, part = self.get("/proxy.mp4", headers={"Range": "bytes=10000-"})
        self.assertEqual(part, whole[10000:])
        self.assertEqual(self.get("/proxy.mp4", headers={"Range": "bytes=99999-"})[0].status, 416)
        self.assertEqual(self.req("HEAD", "/proxy.mp4")[0].getheader("Content-Length"), str(len(whole)))

    def test_autosave_and_recover(self):
        pcm = b"\x01\x00" * 48000
        self.post("/api/autosave?sr=48000&reset=1", pcm)
        self.post("/api/autosave?sr=48000", pcm + b"\x07")  # an odd trailing byte is dropped
        self.assertAlmostEqual(self.js(self.get("/api/state"))["autosave"], 2.0, places=3)
        r = self.js(self.post("/api/autosave_recover"))
        self.assertEqual(r["file"], "full_t1.wav")
        with wave.open(os.path.join(self.data, "recordings", "full_t1.wav")) as w:
            self.assertEqual((w.getframerate(), w.getnframes()), (48000, 96000))
        self.assertEqual(self.js(self.get("/api/state"))["autosave"], 0)
        self.assertEqual(self.post("/api/autosave_recover")[0].status, 404)
        self.post("/api/autosave?sr=48000&reset=1", pcm)
        self.post("/api/autosave_clear")
        self.assertEqual(self.js(self.get("/api/state"))["autosave"], 0)
        self.wait_jobs()

    def test_roomtone_notes_cues_export(self):
        self.assertEqual(self.post("/api/roomtone", b"junk")[0].status, 400)
        self.post("/api/roomtone", wav_bytes(0.5))
        self.assertTrue(self.js(self.get("/api/state"))["roomtone"])
        self.assertEqual(self.get("/rec/_roomtone.wav")[0].status, 200)

        notes = "[0:00] नमस्ते दोस्तों\r\nline two"
        self.post("/api/notes", notes.encode("utf-8"))
        self.assertEqual(self.js(self.get("/api/notes"))["text"], notes)

        cues = [{"t": 1.234, "label": "A" * 200, "kind": "custom"}, {"t": 3}]
        self.post("/api/cues", json.dumps(cues).encode())
        got = self.js(self.get("/api/cues"))
        self.assertEqual(got["video"], "clip.mp4")
        self.assertEqual(got["cues"][0], {"t": 1.23, "label": "A" * 80, "kind": "custom"})
        self.assertEqual(got["cues"][1]["kind"], "custom")
        self.post("/api/cues_reset")
        self.assertEqual([c["label"] for c in self.js(self.get("/api/cues"))["cues"]], ["Intro", "Part 2"])

        self.assertFalse(self.js(self.post("/api/export"))["ok"])  # nothing recorded yet
        self.add_take(1.0)
        self.wait_jobs()
        r = self.js(self.post("/api/export"))
        self.assertTrue(r["ok"])
        self.assertEqual((r["file"], r["clean"]), ("full_t1.wav", "_clean/full_t1.wav"))
        man = json.load(open(r["path"], encoding="utf-8"))
        self.assertEqual(man["video_offset_sec"], 0.25)
        self.assertEqual(man["roomtone"], "_roomtone.wav")
        self.assertEqual(len(man["cues"]), 2)

    def test_corrupt_meta_does_not_break_the_studio(self):
        self.add_take(1.0)
        self.wait_jobs()
        with open(os.path.join(self.data, "recordings", "meta.json"), "w") as f:
            f.write("{broken")
        st = self.js(self.get("/api/state"))
        self.assertEqual(st["takes"]["full"][0]["file"], "full_t1.wav")
        self.assertEqual(self.add_take(1.0)["n"], 2)
        self.wait_jobs()


class SetTests(Base):
    def make_set(self, name, script):
        d = os.path.join(self.data, "sets", name)
        os.makedirs(d)
        with open(os.path.join(d, "proxy.mp4"), "wb") as f:
            f.write(b"set-" + name.encode())
        with open(os.path.join(d, "my-script.txt"), "w", encoding="utf-8") as f:
            f.write(script)
        json.dump({"name": name + ".mp4", "duration": 5}, open(os.path.join(d, "video.json"), "w"))
        json.dump({name + ".mp4": [{"t": 1, "label": "S", "kind": "section"}]}, open(os.path.join(d, "cues.json"), "w"))

    def test_switch_archives_takes_and_loads_the_set(self):
        self.make_set("alpha", "alpha script")
        self.make_set("beta", "beta script")
        os.makedirs(os.path.join(self.data, "sets", "no-video"))  # not a usable set
        self.assertEqual(self.js(self.get("/api/info"))["sets"], ["alpha", "beta"])

        self.assertEqual(self.post("/api/sets/switch?name=alpha")[0].status, 200)
        self.add_take(1.0)
        self.wait_jobs()
        r = self.js(self.post("/api/sets/switch?name=beta"))
        self.assertEqual((r["previous"], r["current"]), ("alpha", "beta"))
        self.assertEqual(self.js(self.get("/api/notes"))["text"], "beta script")
        self.assertEqual(self.get("/proxy.mp4")[1], b"set-beta")
        self.assertEqual(self.js(self.get("/api/state"))["takes"], {})
        self.assertTrue(os.path.isfile(os.path.join(self.data, "recordings_archive", "alpha", "full_t1.wav")))
        self.assertEqual(self.js(self.get("/api/info"))["current_set"], "beta")

        # switching back and recording again must not overwrite the archived take of the same name
        self.post("/api/sets/switch?name=alpha")
        self.add_take(1.0)
        self.wait_jobs()
        self.post("/api/sets/switch?name=beta")
        wavs = [f for f in os.listdir(os.path.join(self.data, "recordings_archive", "alpha")) if f.startswith("full_t1")]
        self.assertEqual(len(wavs), 2, wavs)

    def test_switch_refuses_unknown_names_and_busy_studio(self):
        self.make_set("alpha", "a")
        for bad in ("nope", "..", "../alpha", ""):
            self.assertEqual(self.post("/api/sets/switch?name=" + bad)[0].status, 404, bad)
        self.app.jobs.video["state"] = "working"
        self.assertEqual(self.post("/api/sets/switch?name=alpha")[0].status, 409)
        self.app.jobs.video["state"] = "idle"

    def test_cli_switch(self):
        self.make_set("gamma", "g")
        r = subprocess.run([sys.executable, "-m", "voice_studio", "switch", "gamma", "--data", self.data],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sets.current(Store(self.data)), "gamma")


@unittest.skipUnless(config.capabilities(config.DEFAULTS)["ffmpeg"], "ffmpeg not installed")
class VideoTests(Base):
    def test_change_video(self):
        src = os.path.join(self.tmp, "in.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", src], check=True)
        r = self.post("/api/setvideo?name=" + "My%20Clip%0A.mp4", open(src, "rb").read())
        self.assertEqual(r[0].status, 200, r[1])
        end = time.time() + 60
        while self.js(self.get("/api/video_status"))["state"] == "working" and time.time() < end:
            time.sleep(0.3)
        st = self.js(self.get("/api/video_status"))
        self.assertEqual(st["state"], "done", st)
        self.assertEqual(st["video"]["name"], "My Clip.mp4")  # control characters stripped
        self.assertAlmostEqual(st["video"]["duration"], 2.0, delta=0.2)
        self.assertTrue(os.path.isfile(os.path.join(self.data, "proxy_previous.mp4")))
        self.assertEqual(self.post("/api/setvideo?name=x", b"")[0].status, 400)

    def test_bad_video_reports_error(self):
        self.post("/api/setvideo?name=bad.mp4", b"this is not a video" * 100)
        end = time.time() + 30
        while self.js(self.get("/api/video_status"))["state"] == "working" and time.time() < end:
            time.sleep(0.2)
        st = self.js(self.get("/api/video_status"))
        self.assertEqual(st["state"], "error")
        self.assertEqual(st["video"]["name"], "clip.mp4")  # the old picture stays


if __name__ == "__main__":
    unittest.main()
