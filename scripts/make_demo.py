"""Build a demo project (no personal or third-party footage) for screenshots and trying the studio:
python scripts/make_demo.py <folder>   then   python -m voice_studio --data <folder> --open
Needs ffmpeg on PATH."""
import json
import math
import os
import struct
import subprocess
import sys
import wave

SECTIONS = [(0, "Hook", "THE 60-SECOND REVIEW"), (8, "What it is", "WHAT IT IS"), (18, "Three reasons", "3 REASONS TO CARE"),
            (32, "The catch", "THE CATCH"), (42, "Verdict", "VERDICT"), (52, "Outro", "SEE YOU NEXT TIME")]
DUR = 60
SCRIPT = """[0:00] HOOK
You have never heard a voice-over this clean. Give me sixty seconds.

[0:08] WHAT IT IS
Voice Studio plays your edit with the sound off, and you talk over it in one go.

[0:18] THREE REASONS
One: every slip is flagged. Two: every take is kept. Three: the picture waits for you.

[0:32] THE CATCH
It only records. The cutting happens in your editor.

[0:42] VERDICT
If you narrate your videos, this saves an hour a week.

[0:52] OUTRO
Subscribe, and see you in the next one.
"""


def font():
    for f in (r"C:\Windows\Fonts\segoeuib.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.isfile(f):
            return f.replace("\\", "/").replace(":", "\\:")
    sys.exit("no bold font found")


def tone_wav(path, sec, f0, seed):
    sr = 48000
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(int(sec * sr)):
            t = i / sr
            env = max(0.0, math.sin(t * 2.6 + seed)) ** 0.6 * (0.55 + 0.45 * math.sin(t * 0.7 + seed))  # speech-like bursts
            frames += struct.pack("<h", int(8000 * env * math.sin(2 * math.pi * f0 * t)))
        w.writeframes(bytes(frames))


def main(out):
    os.makedirs(os.path.join(out, "recordings", "_clean"), exist_ok=True)
    os.makedirs(os.path.join(out, "recordings", "_transcripts"), exist_ok=True)
    fnt = font()
    draw = []
    for i, (t, _, title) in enumerate(SECTIONS):
        end = SECTIONS[i + 1][0] if i + 1 < len(SECTIONS) else DUR
        en = "enable='between(t,%d,%d)'" % (t, end)
        draw.append("drawtext=fontfile='%s':text='%s':fontsize=84:fontcolor=white:x=(w-tw)/2:y=(h-th)/2-30:shadowcolor=black@0.35:shadowx=3:shadowy=3:%s" % (fnt, title, en))
        draw.append("drawtext=fontfile='%s':text='PART %d':fontsize=30:fontcolor=white@0.75:x=(w-tw)/2:y=(h-th)/2+60:%s" % (fnt, i + 1, en))
    vf = ",".join(draw)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "gradients=s=1280x720:d=%d:speed=0.012:c0=0x2b1055:c1=0x7597de:c2=0xd76d77:c3=0xffaf7b:n=4" % DUR,
                    "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-g", "30",
                    "-movflags", "+faststart", os.path.join(out, "proxy.mp4")], check=True)
    cues = {"demo.mp4": [{"t": t, "label": label, "kind": "section"} for t, label, _ in SECTIONS]}
    for name in ("cues.json", "cues-seed.json"):
        json.dump(cues, open(os.path.join(out, name), "w"), indent=1)
    json.dump({"name": "demo.mp4", "duration": float(DUR)}, open(os.path.join(out, "video.json"), "w"))
    open(os.path.join(out, "my-script.txt"), "w", encoding="utf-8").write(SCRIPT)

    rec = os.path.join(out, "recordings")
    meta = {"selected": {"full": 3}, "sec": {}, "offset": {}, "segs": {}, "peak": {}}
    for n, (sec, f0) in enumerate(((31.0, 190), (58.4, 210), (61.2, 200)), 1):
        fn = "full_t%d.wav" % n
        tone_wav(os.path.join(rec, fn), sec, f0, n)
        meta["sec"][fn], meta["offset"][fn], meta["peak"][fn] = sec, 0.4, 0.245
        meta["segs"][fn] = [{"ev": "start", "t": 0, "wav": 0, "vid": 0}, {"ev": "redo", "t": 21.2, "wav": 21.2, "vid": 21.0}]
    for n in (2, 3):
        tone_wav(os.path.join(rec, "_clean", "full_t%d.wav" % n), 2, 200, n)
        lines = ["Transcript of full_t%d.wav." % n, "", "WITH TIMES", ""] + ["[%d:%04.1f] %s" % (t // 60, t % 60 + 0.4, l.split("\n")[1])
                 for t, l in zip([s[0] for s in SECTIONS], SCRIPT.strip().split("\n\n"))]
        open(os.path.join(rec, "_transcripts", "full_t%d_script.txt" % n), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    json.dump(meta, open(os.path.join(rec, "meta.json"), "w"), indent=1)
    open(os.path.join(rec, "_roomtone.wav"), "wb").write(open(os.path.join(rec, "full_t1.wav"), "rb").read()[:44 + 960000])
    print("demo project ready in", out)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
