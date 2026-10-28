"""Projects ("sets"): each folder in sets/ holds proxy.mp4, my-script.txt, cues.json, video.json.
Switching archives the current takes to recordings_archive/<previous set>, then loads the chosen set."""
import os
import re
import shutil
import time

from .store import retry

SET_NAME = re.compile(r"^[\w.-]{1,60}$")
SET_FILES = ("proxy.mp4", "my-script.txt", "cues.json", "video.json")


def _dir(store):
    return os.path.join(store.root, "sets")


def current(store):
    try:
        with open(os.path.join(store.root, "current_set.txt"), encoding="utf-8-sig") as f:
            return f.read().strip() or None
    except OSError:
        return None


def available(store):
    d = _dir(store)
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d) if SET_NAME.match(n) and os.path.isfile(os.path.join(d, n, "proxy.mp4")))


def _move_into(src, dst_dir):
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst):  # never overwrite or merge into an older archive: keep both
        base, ext = os.path.splitext(dst) if os.path.isfile(src) else (dst, "")
        dst = base + time.strftime("_%Y%m%d-%H%M%S") + ext
    shutil.move(src, dst)


def switch(store, name):
    if not SET_NAME.match(name or "") or name not in available(store):
        raise ValueError("No set called %r." % name)
    prev = current(store) or "default"
    src = os.path.join(_dir(store), name)
    with store.lock:
        for f in SET_FILES:  # load the new set first: if a file is locked, nothing has been archived yet
            if os.path.isfile(os.path.join(src, f)):
                retry(lambda: shutil.copyfile(os.path.join(src, f), os.path.join(store.root, f)), 40)
        if prev != name:
            arch = os.path.join(store.root, "recordings_archive", prev)
            os.makedirs(arch, exist_ok=True)
            for f in os.listdir(store.rec):
                retry(lambda: _move_into(os.path.join(store.rec, f), arch))
    with open(os.path.join(store.root, "current_set.txt"), "w", encoding="utf-8") as f:
        f.write(name)
    return prev
