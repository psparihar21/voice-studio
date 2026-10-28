"""python -m voice_studio [serve] [--data DIR] [--port N] [--open]
   python -m voice_studio switch SET [--data DIR]"""
import argparse
import logging
import logging.handlers
import os
import sys
import webbrowser

from . import __version__, config, sets
from .server import make_server
from .store import Store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="voice_studio", description="Voice Studio %s" % __version__)
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "switch"])
    ap.add_argument("set", nargs="?", help="set name for 'switch'")
    ap.add_argument("--data", default=config.default_data_dir(), help="folder with your takes and sets (default: %(default)s)")
    ap.add_argument("--port", type=int, help="override the port from config.json")
    ap.add_argument("--open", action="store_true", help="open the studio in the default browser")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)

    os.makedirs(a.data, exist_ok=True)
    if a.command == "switch":
        if not a.set:
            ap.error("switch needs a set name; sets: %s" % ", ".join(sets.available(Store(a.data))))
        try:
            prev = sets.switch(Store(a.data), a.set)
        except ValueError as e:
            sys.exit(str(e))
        print("loaded %s (previous takes in recordings_archive\\%s)" % (a.set, prev))
        return

    cfg = config.load(a.data)
    if a.port is not None:
        cfg["port"] = a.port
    h = logging.handlers.RotatingFileHandler(os.path.join(a.data, "voice-studio.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("voice_studio").addHandler(h)
    logging.getLogger("voice_studio").setLevel(logging.INFO)
    try:
        srv, app = make_server(a.data, cfg)
    except OSError as e:
        sys.exit("Port %d is busy (%s). Is Voice Studio already running?" % (cfg["port"], e))
    url = "http://localhost:%d" % app.port
    print("Voice Studio %s on %s  (takes are saved to %s)" % (__version__, url, app.store.rec), flush=True)
    missing = [k for k, ok in app.caps.items() if not ok]
    if missing:
        print("Not available on this computer: %s (see README, Optional tools)" % ", ".join(missing), flush=True)
    if a.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
