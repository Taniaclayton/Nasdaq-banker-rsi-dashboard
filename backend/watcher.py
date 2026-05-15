"""
Auto-loader watcher
====================
Watches your EOD data folder for new NASDAQ_*.txt files.
When a new file appears, automatically runs the loader to
compute signals and insert into MySQL.

Setup:
    1. Set EOD_FOLDER in your .env file (or edit WATCH_FOLDER below).
    2. pip install -r loader_requirements.txt
    3. python watcher.py

Leave it running in a terminal; press Ctrl+C to stop.
"""

import os
import sys
import time
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from dotenv import load_dotenv

load_dotenv()

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
# Set EOD_FOLDER in your .env, or change the fallback path here.
WATCH_FOLDER  = os.getenv("EOD_FOLDER", str(Path(__file__).parent.parent / "data"))
LOADER_SCRIPT = str(Path(__file__).parent / "loader.py")
PYTHON        = sys.executable   # uses the same Python that's running this script
# ─────────────────────────────────────────────────────────────────────────────

os.environ["PYTHONIOENCODING"] = "utf-8"
running = False  # prevent overlapping runs


class NewFileHandler(FileSystemEventHandler):
    def on_created(self, event):
        global running
        path = Path(event.src_path)

        if event.is_directory:
            return
        if not (path.name.startswith("NASDAQ_") and path.suffix == ".txt"):
            return
        if running:
            print(f"[watcher] Still processing previous file, skipping {path.name}")
            return

        print(f"\n[watcher] New file detected: {path.name}")
        _run_loader()

    def on_moved(self, event):
        """Also catches files moved/copied into the folder."""
        fake = type("E", (), {"src_path": event.dest_path, "is_directory": event.is_directory})()
        self.on_created(fake)


def _run_loader():
    global running
    running = True
    print("[watcher] Running loader...")
    try:
        result = subprocess.run(
            [PYTHON, LOADER_SCRIPT],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("[watcher] ✓ Loader finished successfully.")
            out = result.stdout
            print(out[-800:] if len(out) > 800 else out)
        else:
            print("[watcher] ✗ Loader failed:")
            err = result.stderr
            print(err[-800:] if len(err) > 800 else err)
    except Exception as e:
        print(f"[watcher] Error running loader: {e}")
    finally:
        running = False


if __name__ == "__main__":
    watch_path = Path(WATCH_FOLDER)
    if not watch_path.exists():
        print(f"ERROR: Watch folder not found: {WATCH_FOLDER}")
        print("Set EOD_FOLDER in your .env file or create the data/ directory.")
        sys.exit(1)

    print(f"[watcher] Watching: {WATCH_FOLDER}")
    print(f"[watcher] Loader:   {LOADER_SCRIPT}")
    print(f"[watcher] Waiting for new NASDAQ_*.txt files... (Ctrl+C to stop)\n")

    observer = Observer()
    observer.schedule(NewFileHandler(), str(watch_path), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\n[watcher] Stopped.")
    observer.join()
