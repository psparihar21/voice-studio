"""Everything on disk: takes, their metadata, flags, notes, crash autosave. JSON writes are atomic."""
import json
import os
import re
import shutil
import threading
import time
import wave

NAME = re.compile(r"^(c\d+|full)_t(\d+)\.wav$")
FULL = re.compile(r"^full_t\d+\.wav$")
CUE = re.compile(r"^(c\d+|full)$")
META_KEYS = ("selected", "sec", "offset", "segs", "peak")


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj, indent=1):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    retry(lambda: os.replace(tmp, path))


def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    retry(lambda: os.replace(tmp, path))


def retry(fn, tries=12):
    """Windows refuses to move or delete a file while a player still holds it, so try for ~3 s."""
    for i in range(tries):
        try:
            return fn()
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.25)


def remove_file(path):
    try:
        retry(lambda: os.path.exists(path) and os.remove(path))
        return True
    except PermissionError:
        return False


class Store:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        j = os.path.join
        self.rec = j(self.root, "recordings")
        self.tr = j(self.rec, "_transcripts")
        self.clean_dir = j(self.rec, "_clean")
        self.trash = j(self.rec, "_trash")
        self.meta_path = j(self.rec, "meta.json")
        self.cues_path = j(self.root, "cues.json")
        self.seed_path = j(self.root, "cues-seed.json")
        self.notes_path = j(self.root, "my-script.txt")
        self.video_json = j(self.root, "video.json")
        self.proxy = j(self.root, "proxy.mp4")
        self.roomtone = j(self.rec, "_roomtone.wav")
        self.as_raw = j(self.rec, "_autosave.pcm")
        self.as_sr = j(self.rec, "_autosave.sr")
        os.makedirs(self.rec, exist_ok=True)
        self.lock = threading.RLock()  # meta.json and cues.json are read-modify-write

    # ---- paths ----
    def wav(self, fn):
        return os.path.join(self.rec, fn)

    def txt_path(self, fn):
        return os.path.join(self.tr, fn[:-4] + "_script.txt")

    def clean_path(self, fn):
        return os.path.join(self.clean_dir, fn)

    # ---- meta ----
    def load_meta(self):
        m = read_json(self.meta_path, {})
        if not isinstance(m, dict):
            m = {}
        for k in META_KEYS:
            if not isinstance(m.get(k), dict):
                m[k] = {}
        return m

    def save_meta(self, m):
        write_json(self.meta_path, m)

    def takes(self):
        out = {}
        for f in sorted(os.listdir(self.rec)):
            mt = NAME.match(f)
            if mt:
                out.setdefault(mt.group(1), []).append({"n": int(mt.group(2)), "file": f})
        for v in out.values():
            v.sort(key=lambda x: x["n"])  # numeric: t2 before t10
        return out

    def next_n(self, cue):
        return max([t["n"] for t in self.takes().get(cue, [])] + [0]) + 1

    # ---- video + flags + notes ----
    def video_info(self):
        v = read_json(self.video_json, None)
        return v if isinstance(v, dict) else {"name": "proxy.mp4", "duration": None}

    def _video_key(self):
        return self.video_info().get("name") or "proxy.mp4"

    def cues(self):
        allc = read_json(self.cues_path, {})
        return allc.get(self._video_key(), []) if isinstance(allc, dict) else []

    def save_cues(self, lst):
        with self.lock:
            allc = read_json(self.cues_path, {})
            allc = allc if isinstance(allc, dict) else {}
            allc[self._video_key()] = lst
            write_json(self.cues_path, allc)

    def reset_cues(self):
        seed = read_json(self.seed_path, None)
        if not isinstance(seed, dict):
            return False
        self.save_cues(seed.get(self._video_key(), []))
        return True

    def notes(self):
        try:
            with open(self.notes_path, encoding="utf-8", newline="") as f:
                return f.read()
        except OSError:
            return ""

    def save_notes(self, text):
        write_text(self.notes_path, text)

    def save_take_script(self, fn, text):
        os.makedirs(self.tr, exist_ok=True)
        write_text(self.txt_path(fn), text)

    # ---- takes ----
    def add_take(self, cue, data, sec, offset, segs, peak):
        with self.lock:
            n = self.next_n(cue)
            fn = "%s_t%d.wav" % (cue, n)
            tmp = self.wav(fn) + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, self.wav(fn))
            m = self.load_meta()
            m["sec"][fn], m["offset"][fn], m["peak"][fn] = sec, offset, peak
            if segs is not None:
                m["segs"][fn] = segs
            m["selected"][cue] = n
            self.save_meta(m)
        return n, fn

    def select(self, cue, n):
        with self.lock:
            m = self.load_meta()
            m["selected"][cue] = n
            self.save_meta(m)

    def delete(self, fn):
        """Move a take to _trash with its metadata, so it can be restored. Raises PermissionError if Windows holds it."""
        cue = NAME.match(fn).group(1)
        with self.lock:
            os.makedirs(self.trash, exist_ok=True)
            m = self.load_meta()
            tn = "%s__%s.wav" % (fn[:-4], time.strftime("%Y%m%d-%H%M%S"))  # unique: a reused take name never overwrites the trash
            while os.path.exists(os.path.join(self.trash, tn)):
                tn = tn[:-4] + "x.wav"
            try:
                retry(lambda: shutil.move(self.wav(fn), os.path.join(self.trash, tn)))
            except FileNotFoundError:
                pass
            write_json(os.path.join(self.trash, tn + ".json"), {k: m[k].get(fn) for k in ("sec", "offset", "segs", "peak")})
            for k in ("sec", "offset", "segs", "peak"):
                m[k].pop(fn, None)
            remove_file(self.txt_path(fn))
            remove_file(self.clean_path(fn))
            rest = [t["n"] for t in self.takes().get(cue, [])]
            if rest:
                if m["selected"].get(cue) not in rest:
                    m["selected"][cue] = rest[-1]
            else:
                m["selected"].pop(cue, None)
            self.save_meta(m)

    def restore(self, fn):
        """Bring a deleted take back as the newest take. Returns (n, new_file) or None."""
        if not NAME.match(fn) or not os.path.isdir(self.trash):
            return None
        stem = fn[:-4] + "__"
        found = sorted(f for f in os.listdir(self.trash) if f.startswith(stem) and f.endswith(".wav"))
        if found:
            tf = os.path.join(self.trash, found[-1])  # the newest deleted copy of that take
        elif os.path.isfile(os.path.join(self.trash, fn)):
            tf = os.path.join(self.trash, fn)  # trashed by version 1
        else:
            return None
        cue = NAME.match(fn).group(1)
        with self.lock:
            n = self.next_n(cue)
            nf = "%s_t%d.wav" % (cue, n)
            shutil.move(tf, self.wav(nf))
            m = self.load_meta()
            old = read_json(tf + ".json", {})
            for k in ("sec", "offset", "segs", "peak"):
                if isinstance(old, dict) and old.get(k) is not None:
                    m[k][nf] = old[k]
            remove_file(tf + ".json")
            m["selected"][cue] = n
            self.save_meta(m)
        return n, nf

    def save_roomtone(self, data):
        with open(self.roomtone + ".part", "wb") as f:
            f.write(data)
        os.replace(self.roomtone + ".part", self.roomtone)

    # ---- crash autosave: raw 16-bit mono PCM appended every few seconds while recording ----
    def autosave_write(self, data, sr, reset):
        if reset:
            with open(self.as_sr, "w") as f:
                f.write(str(sr))
        with open(self.as_raw, "wb" if reset else "ab") as f:
            f.write(data)

    def _as_rate(self):
        try:
            with open(self.as_sr) as f:
                return int(f.read())
        except (OSError, ValueError):
            return 0

    def autosave_sec(self):
        sr = self._as_rate()
        try:
            return os.path.getsize(self.as_raw) / 2 / sr if sr else 0
        except OSError:
            return 0

    def autosave_clear(self):
        remove_file(self.as_raw)
        remove_file(self.as_sr)

    def autosave_recover(self):
        sr = self._as_rate()
        if self.autosave_sec() <= 0:
            return None
        with open(self.as_raw, "rb") as f:
            raw = f.read()
        raw = raw[: len(raw) // 2 * 2]
        with self.lock:
            n = self.next_n("full")
            fn = "full_t%d.wav" % n
            with wave.open(self.wav(fn), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(raw)
            m = self.load_meta()
            m["sec"][fn] = len(raw) / 2 / sr
            m["offset"][fn] = 0.0
            m["selected"]["full"] = n
            self.save_meta(m)
        self.autosave_clear()
        return n, fn

    # ---- hand-off for the edit ----
    def export(self):
        m = self.load_meta()
        sel = m["selected"].get("full")
        fn = "full_t%d.wav" % sel if sel else None
        if fn and not os.path.isfile(self.wav(fn)):
            fn = None
        man = {
            "mode": "full-take",
            "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
            "file": fn,
            "clean_file": ("_clean/" + fn) if fn and os.path.isfile(self.clean_path(fn)) else None,
            "sec": m["sec"].get(fn) if fn else None,
            "video_offset_sec": m["offset"].get(fn) if fn else None,
            "segments": m["segs"].get(fn) if fn else None,
            "cues": self.cues(),
            "roomtone": "_roomtone.wav" if os.path.exists(self.roomtone) else None,
        }
        path = os.path.join(self.rec, "manifest.json")
        write_json(path, man, indent=2)
        return man, path
