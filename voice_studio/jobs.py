"""Background work: transcript (GPU Whisper), voice cleanup (DeepFilterNet), preparing a new picture (ffmpeg).
Each runs in a thread; Whisper jobs queue on one lock so two never fight for the GPU. Originals are never modified."""
import json
import logging
import os
import shutil
import subprocess
import threading
import time

from .store import retry, write_json

log = logging.getLogger("voice_studio")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SCALE = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)


def tail(r, fallback):
    return (r.stderr or r.stdout or fallback)[-300:]


class Jobs:
    def __init__(self, store, cfg):
        self.s, self.cfg = store, cfg
        self.gpu = threading.Lock()      # Whisper
        self.cpu = threading.Lock()      # cleanup runs beside Whisper, one at a time, like before
        self.transcripts = {}  # file -> {"state": "running"|"done"|"error", "msg": str}
        self.cleans = {}
        self.video = {"state": "idle", "msg": ""}
        self.table_lock = threading.Lock()  # check-then-start must be atomic

    def busy(self):
        return (self.video["state"] == "working"
                or any(j["state"] == "running" for j in list(self.transcripts.values()) + list(self.cleans.values())))

    def running(self, fn):
        return any(t.get(fn, {}).get("state") == "running" for t in (self.transcripts, self.cleans))

    def claim_video(self):
        with self.table_lock:
            if self.video["state"] == "working":
                return False
            self.video.update(state="working", msg="Receiving the video…")
            return True

    def forget(self, fn):
        self.transcripts.pop(fn, None)
        self.cleans.pop(fn, None)

    def _spawn(self, table, fn, target, lock):
        with self.table_lock:
            if table.get(fn, {}).get("state") == "running":
                return
            table[fn] = {"state": "running", "msg": ""}
        threading.Thread(target=self._guard, args=(table, fn, target, lock), daemon=True).start()

    def _guard(self, table, fn, target, lock):
        with lock:
            try:
                target(fn)
                table[fn] = {"state": "done", "msg": ""}
            except Exception as e:  # reported to the UI as the job's error
                log.exception("job failed for %s", fn)
                table[fn] = {"state": "error", "msg": str(e)[-300:]}

    # ---- transcript ----
    def transcript_state(self, fn):
        j = self.transcripts.get(fn)
        if j:
            return j
        return {"state": "done" if os.path.isfile(self.s.txt_path(fn)) else "none", "msg": ""}

    def start_transcribe(self, fn):
        if os.path.isfile(self.s.txt_path(fn)):
            return
        self._spawn(self.transcripts, fn, self._transcribe, self.gpu)

    def _transcribe(self, fn):
        c = self.cfg
        py, sk = c["whisper_python"], c["whisper_scripts"]
        if not os.path.isfile(py):
            raise RuntimeError("Transcription is not set up on this computer (whisper_python not found).")
        d = os.path.join(self.s.tr, fn[:-4])
        os.makedirs(d, exist_ok=True)
        cmd = [py, "-I", os.path.join(sk, "whisper_gpu.py"), self.s.wav(fn), d, "--language", c["language"]]
        if c["vocab_prompt"]:
            cmd += ["--prompt", c["vocab_prompt"]]
        r = run(cmd)
        if r.returncode != 0:
            raise RuntimeError(tail(r, "transcription failed"))
        run([py, "-I", os.path.join(sk, "romanize.py"), "draft", os.path.join(d, "transcript.json"), d])  # optional: Roman letters
        self.build_txt(fn, d)

    def build_txt(self, fn, d):
        def load(name):
            with open(os.path.join(d, name), encoding="utf-8") as f:
                return json.load(f)
        dev = load("transcript.json")
        try:
            rom = load("transcript.roman.json")
        except (OSError, ValueError):
            rom = dev
        os.makedirs(self.s.tr, exist_ok=True)
        if not dev:
            self.s.save_take_script(fn, "No speech was detected in %s." % fn)
            return
        lines, cur, last = [], [], None
        for i, w in enumerate(dev):
            r = rom[i]["text"] if i < len(rom) else w["text"]
            if cur and (w["start"] - last > 0.7 or len(cur) >= 14):
                lines.append((cur[0][0], " ".join(x[1] for x in cur), " ".join(x[2] for x in cur)))
                cur = []
            cur.append((w["start"], r, w["text"]))
            last = w["end"]
        if cur:
            lines.append((cur[0][0], " ".join(x[1] for x in cur), " ".join(x[2] for x in cur)))
        mmss = lambda t: "%d:%04.1f" % (int(t // 60), t % 60)
        out = ["Transcript of %s (Whisper, Hinglish in English letters)." % fn,
               "Auto-generated: spellings and numbers may need a quick check.", "", "WITH TIMES", ""]
        out += ["[%s] %s" % (mmss(t), r) for t, r, _ in lines]
        out += ["", "PLAIN TEXT", "", " ".join(r for _, r, _ in lines), "",
                "ORIGINAL SCRIPT (as the speech model wrote it)", "", " ".join(x for _, _, x in lines), ""]
        self.s.save_take_script(fn, "\n".join(out))

    # ---- voice cleanup ----
    def clean_state(self, fn):
        if os.path.isfile(self.s.clean_path(fn)):
            return "done"
        return self.cleans.get(fn, {}).get("state")

    def start_clean(self, fn):
        if os.path.isfile(self.s.clean_path(fn)):
            return
        if self.cleans.get(fn, {}).get("state") == "error":
            self.cleans.pop(fn)
        self._spawn(self.cleans, fn, self._clean, self.cpu)

    def _clean(self, fn):
        """Gentle denoise + presence EQ into _clean/. The original take is never touched."""
        c = self.cfg
        if not os.path.isfile(c["enhance_script"]):
            raise RuntimeError("Voice cleanup is not set up on this computer (enhance_script not found).")
        os.makedirs(self.s.clean_dir, exist_ok=True)
        tmp = self.s.clean_path(fn[:-4] + "_tmp.wav")
        r = run([c["enhance_python"], c["enhance_script"], self.s.wav(fn), tmp])
        if r.returncode != 0 or not os.path.isfile(tmp):
            raise RuntimeError(tail(r, "cleanup failed"))
        retry(lambda: os.replace(tmp, self.s.clean_path(fn)))

    def after_take(self, fn):
        """A new full take: write its script and clean the voice in the background."""
        self.start_transcribe(fn)
        self.start_clean(fn)

    # ---- new picture ----
    def start_video(self, src_tmp, name):
        self.video.update(state="working", msg="Preparing the video…")
        threading.Thread(target=self._video, args=(src_tmp, name), daemon=True).start()

    def _video(self, src_tmp, name):
        """Turn any video into the silent 1280x720 preview the studio plays. Keeps the old one as proxy_previous.mp4."""
        out_tmp = os.path.join(self.s.root, "_proxy_new.mp4")
        ff = self.cfg["ffmpeg"]
        try:
            head = [ff, "-v", "error", "-y", "-i", src_tmp, "-vf", SCALE]
            tailc = ["-g", "30", "-an", "-movflags", "+faststart", out_tmp]
            cmds = [head + ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "28", "-b:v", "0"] + tailc,
                    head + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"] + tailc]
            ok = False
            for cmd in cmds:  # GPU encoder first, CPU fallback
                r = run(cmd)
                if r.returncode == 0 and os.path.isfile(out_tmp) and os.path.getsize(out_tmp) > 1000:
                    ok = True
                    break
            if not ok:
                raise RuntimeError("Could not read that file as a video.")
            d = run([self.cfg["ffprobe"], "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out_tmp]).stdout.strip()
            if os.path.isfile(self.s.proxy):
                shutil.copyfile(self.s.proxy, os.path.join(self.s.root, "proxy_previous.mp4"))
            retry(lambda: os.replace(out_tmp, self.s.proxy), 40)  # the browser may still be reading the old one
            try:
                dur = float(d)
            except ValueError:
                dur = None
            write_json(self.s.video_json, {"name": name, "duration": dur, "set": time.strftime("%Y-%m-%d %H:%M:%S")})
            self.video.update(state="done", msg="")
        except Exception as e:
            log.exception("video prepare failed")
            self.video.update(state="error", msg=str(e)[-300:])
        finally:
            for f in (src_tmp, out_tmp):
                try:
                    if os.path.isfile(f):
                        os.remove(f)
                except OSError:
                    pass
