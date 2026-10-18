"""Voice Studio server. Runs only on this laptop (127.0.0.1), saves takes straight to ./recordings."""
import http.server, socketserver, json, os, re, shutil, sys, time, urllib.parse, mimetypes, subprocess, threading

ROOT = os.path.dirname(os.path.abspath(__file__))
REC = os.path.join(ROOT, "recordings")
SCRIPT = os.path.join(ROOT, "script.json")
META = os.path.join(REC, "meta.json")
os.makedirs(REC, exist_ok=True)
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
NAME = r"^(c\d+|full)_t(\d+)\.wav$"


def load_meta():
    try:
        m = json.load(open(META, encoding="utf-8"))
    except Exception:
        m = {}
    for k in ("selected", "sec", "offset", "segs", "peak"):
        m.setdefault(k, {})
    return m


def save_meta(m):
    json.dump(m, open(META, "w", encoding="utf-8"), indent=1)


def takes():
    out = {}
    for f in sorted(os.listdir(REC)):
        mt = re.match(NAME, f)
        if mt:
            out.setdefault(mt.group(1), []).append({"n": int(mt.group(2)), "file": f})
    return out


def remove_file(path):
    """Delete with a few retries: Windows refuses while a player still holds the file."""
    for i in range(12):
        try:
            if os.path.exists(path):
                os.remove(path)
            return True
        except PermissionError:
            time.sleep(0.25)
    return False


TR = os.path.join(REC, "_transcripts")
GPU = threading.Lock()
JOBS = {}  # file -> {"state": "running"|"done"|"error", "msg": str}
# Optional: a Python with faster-whisper, used only by the auto-transcribe button. Set VOICE_STUDIO_PY to enable it.
VPY = os.environ.get("VOICE_STUDIO_PY", "")
SK = os.environ.get("VOICE_STUDIO_SCRIPTS", os.path.join(os.path.expanduser("~"), ".claude", "skills", "video-edit", "scripts"))
PROMPT = os.environ.get("VOICE_STUDIO_PROMPT", "")  # product and brand names, to help the transcriber spell them


def txt_path(fn):
    return os.path.join(TR, fn[:-4] + "_script.txt")


def build_txt(fn, d):
    dev = json.load(open(os.path.join(d, "transcript.json"), encoding="utf-8"))
    try:
        rom = json.load(open(os.path.join(d, "transcript.roman.json"), encoding="utf-8"))
    except Exception:
        rom = dev
    if not dev:
        open(txt_path(fn), "w", encoding="utf-8").write("No speech was detected in %s." % fn)
        return
    lines, cur = [], []
    def flush():
        if cur:
            lines.append((cur[0][0], " ".join(w[1] for w in cur), " ".join(w[2] for w in cur)))
    last = None
    for i, w in enumerate(dev):
        r = rom[i]["text"] if i < len(rom) else w["text"]
        if cur and (w["start"] - last > 0.7 or len(cur) >= 14):
            flush(); cur.clear()
        cur.append((w["start"], r, w["text"])); last = w["end"]
    flush()
    mmss = lambda t: "%d:%04.1f" % (int(t // 60), t % 60)
    out = ["Transcript of %s (GPU Whisper large-v3, Hinglish in English letters)." % fn,
           "Auto-generated: spellings and numbers may need a quick check.", "", "WITH TIMES", ""]
    out += ["[%s] %s" % (mmss(t), r) for t, r, _ in lines]
    out += ["", "PLAIN TEXT", "", " ".join(r for _, r, _ in lines), "", "DEVANAGARI (original from the speech model)", "",
            " ".join(x for _, _, x in lines), ""]
    open(txt_path(fn), "w", encoding="utf-8").write(chr(10).join(out))


def start_job(fn):
    if JOBS.get(fn, {}).get("state") == "running":
        return
    JOBS[fn] = {"state": "running", "msg": ""}
    threading.Thread(target=run_job, args=(fn,), daemon=True).start()


def run_job(fn):
    with GPU:
        _run_job(fn)


def _run_job(fn):
    d = os.path.join(TR, fn[:-4]); os.makedirs(d, exist_ok=True)
    try:
        if not os.path.isfile(VPY):
            raise RuntimeError("Whisper environment not found")
        r = subprocess.run([VPY, "-I", os.path.join(SK, "whisper_gpu.py"), os.path.join(REC, fn), d, "--language", "hi", "--prompt", PROMPT],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "transcription failed")[-300:])
        subprocess.run([VPY, "-I", os.path.join(SK, "romanize.py"), "draft", os.path.join(d, "transcript.json"), d],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        build_txt(fn, d)
        JOBS[fn] = {"state": "done", "msg": ""}
    except Exception as e:
        JOBS[fn] = {"state": "error", "msg": str(e)}


NOTES = os.path.join(ROOT, "my-script.txt")
CUES = os.path.join(ROOT, "cues.json")
VIDEO_JSON = os.path.join(ROOT, "video.json")
VJOB = {"state": "idle", "msg": ""}


def video_info():
    try:
        return json.load(open(VIDEO_JSON, encoding="utf-8"))
    except Exception:
        return {"name": "proxy.mp4", "duration": None}


def prepare_video(src_tmp, name):
    """Turn any video the user picks into the silent 1280x720 preview the studio plays."""
    out_tmp = os.path.join(ROOT, "_proxy_new.mp4")
    try:
        VJOB.update(state="working", msg="Preparing the video…")
        cmds = [["ffmpeg", "-v", "error", "-y", "-i", src_tmp, "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p",
                 "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "28", "-b:v", "0", "-g", "30", "-an", "-movflags", "+faststart", out_tmp],
                ["ffmpeg", "-v", "error", "-y", "-i", src_tmp, "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-g", "30", "-an", "-movflags", "+faststart", out_tmp]]
        ok = False
        for c in cmds:
            r = subprocess.run(c, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0 and os.path.isfile(out_tmp) and os.path.getsize(out_tmp) > 10000:
                ok = True
                break
        if not ok:
            raise RuntimeError("Could not read that file as a video.")
        d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out_tmp], capture_output=True, text=True).stdout.strip()
        cur = os.path.join(ROOT, "proxy.mp4")
        if os.path.isfile(cur):
            shutil.copyfile(cur, os.path.join(ROOT, "proxy_previous.mp4"))
        os.replace(out_tmp, cur)
        json.dump({"name": name, "duration": float(d) if d else None, "set": time.strftime("%Y-%m-%d %H:%M:%S")}, open(VIDEO_JSON, "w", encoding="utf-8"))
        VJOB.update(state="done", msg="")
    except Exception as e:
        VJOB.update(state="error", msg=str(e)[-300:])
    finally:
        for f in (src_tmp, out_tmp):
            try:
                if os.path.isfile(f): os.remove(f)
            except OSError:
                pass


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path, ctype=None, in_memory=False, download=None):
        if not os.path.isfile(path):
            return self.send_json({"error": "not found"}, 404)
        size = os.path.getsize(path)
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end = 0, size - 1
        code = 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                if not m.group(1) and m.group(2):
                    start = max(0, size - int(m.group(2)))
                    end = size - 1
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % download)
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        try:
            if in_memory:  # read fully and close the file BEFORE sending, so it can be deleted any time
                with open(path, "rb") as f:
                    f.seek(start)
                    data = f.read(length)
                self.wfile.write(data)
                return
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def body(self):
        n = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            return self.send_file(os.path.join(ROOT, "index.html"), "text/html; charset=utf-8", True)
        if p == "/proxy.mp4":
            return self.send_file(os.path.join(ROOT, "proxy.mp4"), "video/mp4")
        if p == "/api/cues":
            try:
                allc = json.load(open(CUES, encoding="utf-8"))
            except Exception:
                allc = {}
            nm = video_info().get("name")
            return self.send_json({"video": nm, "cues": allc.get(nm, [])})
        if p == "/api/notes":
            try:
                txt = open(NOTES, encoding="utf-8").read()
            except OSError:
                txt = ""
            return self.send_json({"text": txt})
        if p == "/api/video_status":
            return self.send_json(dict(VJOB, video=video_info()))
        if p == "/api/state":
            m = load_meta()
            t = takes()
            for cue, lst in t.items():
                for it in lst:
                    it["sec"] = m["sec"].get(it["file"])
                    it["offset"] = m["offset"].get(it["file"])
                    it["segs"] = m["segs"].get(it["file"])
                    it["script"] = os.path.isfile(txt_path(it["file"]))
                    it["job"] = JOBS.get(it["file"], {}).get("state")
                    it["jobmsg"] = JOBS.get(it["file"], {}).get("msg")
                    it["peak"] = m["peak"].get(it["file"])
                    try:
                        it["mtime"] = os.path.getmtime(os.path.join(REC, it["file"]))
                    except OSError:
                        it["mtime"] = None
            return self.send_json({"takes": t, "selected": m["selected"], "roomtone": os.path.exists(os.path.join(REC, "_roomtone.wav")), "video": video_info()})
        mt = re.match(r"^/script/(full_t\d+)\.txt$", p)
        if mt:
            return self.send_file(txt_path(mt.group(1) + ".wav"), "text/plain; charset=utf-8", True, mt.group(1) + "_script.txt")
        if p == "/api/transcribe_status":
            fn = (urllib.parse.parse_qs(u.query).get("file") or [""])[0]
            j = JOBS.get(fn) or {"state": "done" if os.path.isfile(txt_path(fn)) else "none", "msg": ""}
            return self.send_json(j)
        mt = re.match(r"^/rec/((c\d+|full)_t\d+\.wav|_roomtone\.wav)$", p)
        if mt:
            return self.send_file(os.path.join(REC, mt.group(1)), "audio/wav", True)
        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        if p == "/api/setvideo":
            if VJOB.get("state") == "working":
                return self.send_json({"error": "Still preparing the last video."}, 409)
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0 or n > 8 * 1024 ** 3:
                return self.send_json({"error": "That file is empty or too large."}, 400)
            tmp = os.path.join(ROOT, "_incoming_video.tmp")
            with open(tmp, "wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(1024 * 1024, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            name = os.path.basename((q.get("name") or ["video"])[0])[:120]
            VJOB.update(state="working", msg="Preparing the video…")
            threading.Thread(target=prepare_video, args=(tmp, name), daemon=True).start()
            return self.send_json({"state": "working"})
        data = self.body()
        if p == "/api/cues_reset":
            try:
                seed = json.load(open(os.path.join(ROOT, "cues-seed.json"), encoding="utf-8"))
            except Exception:
                return self.send_json({"error": "no seed"}, 404)
            try:
                allc = json.load(open(CUES, encoding="utf-8"))
            except Exception:
                allc = {}
            nm = video_info().get("name")
            allc[nm] = seed.get(nm, [])
            json.dump(allc, open(CUES, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            return self.send_json({"ok": True})
        if p == "/api/cues":
            try:
                lst = json.loads(data.decode("utf-8"))
                assert isinstance(lst, list) and len(lst) < 500
                lst = [{"t": round(float(c["t"]), 2), "label": str(c.get("label", ""))[:80], "kind": str(c.get("kind", "custom"))[:12]} for c in lst]
            except Exception:
                return self.send_json({"error": "bad cues"}, 400)
            try:
                allc = json.load(open(CUES, encoding="utf-8"))
            except Exception:
                allc = {}
            allc[video_info().get("name")] = lst
            json.dump(allc, open(CUES, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            return self.send_json({"ok": True})
        if p == "/api/notes":
            if len(data) > 2 * 1024 * 1024:
                return self.send_json({"error": "too long"}, 400)
            open(NOTES, "w", encoding="utf-8", newline="").write(data.decode("utf-8", "replace"))
            return self.send_json({"ok": True})
        if p == "/api/script_save":
            fn = (q.get("file") or [""])[0]
            if not re.match(NAME, fn) or len(data) > 2 * 1024 * 1024:
                return self.send_json({"error": "bad request"}, 400)
            os.makedirs(TR, exist_ok=True)
            open(txt_path(fn), "w", encoding="utf-8", newline="").write(data.decode("utf-8", "replace"))
            return self.send_json({"ok": True})
        if p == "/api/take":
            cue = (q.get("cue") or [""])[0]
            if not re.match(r"^(c\d+|full)$", cue) or data[:4] != b"RIFF" or len(data) > 400 * 1024 * 1024:
                return self.send_json({"error": "bad take"}, 400)
            n = max([t["n"] for t in takes().get(cue, [])] + [0]) + 1
            fn = "%s_t%d.wav" % (cue, n)
            open(os.path.join(REC, fn), "wb").write(data)
            m = load_meta()
            m["sec"][fn] = float((q.get("sec") or ["0"])[0])
            m["offset"][fn] = float((q.get("offset") or ["0"])[0])
            try:
                segs = json.loads((q.get("segs") or ["[]"])[0])
                if isinstance(segs, list):
                    m["segs"][fn] = segs
            except Exception:
                pass
            try:
                m["peak"][fn] = float((q.get("peak") or ["0"])[0])
            except ValueError:
                pass
            m["selected"][cue] = n
            save_meta(m)
            if cue == "full":
                start_job(fn)  # transcribe right away so the script is ready when the user asks
            return self.send_json({"n": n, "file": fn})
        if p == "/api/select":
            cue = (q.get("cue") or [""])[0]
            n = int((q.get("n") or ["0"])[0])
            m = load_meta()
            m["selected"][cue] = n
            save_meta(m)
            return self.send_json({"ok": True})
        if p == "/api/delete":
            fn = (q.get("file") or [""])[0]
            mt = re.match(NAME, fn)
            if not mt:
                return self.send_json({"error": "bad file"}, 400)
            trash = os.path.join(REC, "_trash"); os.makedirs(trash, exist_ok=True)
            m = load_meta()
            try:
                for i in range(12):
                    try:
                        shutil.move(os.path.join(REC, fn), os.path.join(trash, fn)); break
                    except PermissionError:
                        if i == 11: raise
                        time.sleep(0.25)
            except FileNotFoundError:
                pass
            except PermissionError:
                return self.send_json({"error": "Windows is still using that file. Stop playback and try again."}, 409)
            json.dump({k: m[k].get(fn) for k in ("sec", "offset", "segs")}, open(os.path.join(trash, fn + ".json"), "w", encoding="utf-8"))
            m["sec"].pop(fn, None)
            m["offset"].pop(fn, None)
            m["segs"].pop(fn, None)
            m["peak"].pop(fn, None)
            try:
                if os.path.isfile(txt_path(fn)): os.remove(txt_path(fn))
            except OSError:
                pass
            JOBS.pop(fn, None)
            rest = [t["n"] for t in takes().get(mt.group(1), [])]
            if rest:
                if m["selected"].get(mt.group(1)) not in rest:
                    m["selected"][mt.group(1)] = rest[-1]
            else:
                m["selected"].pop(mt.group(1), None)
            save_meta(m)
            return self.send_json({"ok": True})
        if p == "/api/transcribe":
            fn = (q.get("file") or [""])[0]
            if not re.match(NAME, fn) or not os.path.isfile(os.path.join(REC, fn)):
                return self.send_json({"error": "bad file"}, 400)
            if os.path.isfile(txt_path(fn)):
                return self.send_json({"state": "done"})
            start_job(fn)
            return self.send_json({"state": "running"})
        if p == "/api/restore":
            fn = (q.get("file") or [""])[0]
            mt = re.match(NAME, fn)
            tf = os.path.join(REC, "_trash", fn)
            if not mt or not os.path.isfile(tf):
                return self.send_json({"error": "nothing to restore"}, 404)
            cue = mt.group(1)
            n = max([t["n"] for t in takes().get(cue, [])] + [0]) + 1
            nf = "%s_t%d.wav" % (cue, n)
            shutil.move(tf, os.path.join(REC, nf))
            m = load_meta()
            try:
                old = json.load(open(tf + ".json", encoding="utf-8")); os.remove(tf + ".json")
                for k in ("sec", "offset", "segs"):
                    if old.get(k) is not None: m[k][nf] = old[k]
            except Exception:
                pass
            m["selected"][cue] = n
            save_meta(m)
            if cue == "full":
                start_job(nf)
            return self.send_json({"ok": True, "file": nf, "n": n})
        if p == "/api/roomtone":
            if data[:4] != b"RIFF":
                return self.send_json({"error": "bad audio"}, 400)
            open(os.path.join(REC, "_roomtone.wav"), "wb").write(data)
            return self.send_json({"ok": True})
        if p == "/api/export":
            m = load_meta()
            sel = m["selected"].get("full")
            fn = "full_t%d.wav" % sel if sel else None
            man = {"mode": "full-take", "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "file": fn, "sec": m["sec"].get(fn) if fn else None,
                   "video_offset_sec": m["offset"].get(fn) if fn else None,
                   "segments": m["segs"].get(fn) if fn else None,
                   "cues": (json.load(open(CUES, encoding="utf-8")).get(video_info().get("name"), []) if os.path.isfile(CUES) else []),
                   "roomtone": "_roomtone.wav" if os.path.exists(os.path.join(REC, "_roomtone.wav")) else None}
            json.dump(man, open(os.path.join(REC, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            return self.send_json({"ok": bool(fn), "path": os.path.join(REC, "manifest.json"), "file": fn})
        if p == "/api/openfolder":
            try:
                os.startfile(REC)
            except Exception:
                pass
            return self.send_json({"ok": True})
        return self.send_json({"error": "not found"}, 404)


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    print("Voice Studio on http://localhost:%d  (takes are saved to %s)" % (PORT, REC), flush=True)
    S(("127.0.0.1", PORT), H).serve_forever()
