"""
Folder-watch script: blijft draaien en verwerkt elke nieuwe PDF in inbox/ automatisch.

Gebruik:
    python watch.py                          # default: alleen DRAFT PO
    python watch.py --auto-confirm           # bevestig PO automatisch bij volledige match
    python watch.py --leverancier-hint Reimo # default hint voor alle PDFs

Stoppen: Ctrl+C
"""
import os
import sys
import time
import argparse
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
INBOX = BASE_DIR / "inbox"


class PDFHandler(FileSystemEventHandler):
    def __init__(self, args):
        self.args = args
        self.processing = set()

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".pdf":
            return
        if path.name in self.processing:
            return
        self.processing.add(path.name)
        # Wacht even tot het bestand volledig is geschreven
        time.sleep(2)
        if not path.exists():
            return
        print(f"\n🆕 Nieuwe PDF: {path.name}")
        cmd = [sys.executable, str(BASE_DIR / "verwerk.py"), str(path)]
        if self.args.leverancier_hint:
            cmd += ["--leverancier-hint", self.args.leverancier_hint]
        if self.args.auto_confirm:
            cmd += ["--auto-confirm"]
        if self.args.analytic:
            cmd += ["--analytic", str(self.args.analytic)]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"❌ Verwerking faalde voor {path.name}: {e}")
        finally:
            self.processing.discard(path.name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--leverancier-hint", help="Default leverancier-hint")
    parser.add_argument("--auto-confirm", action="store_true")
    parser.add_argument("--analytic", type=int, help="Default project analytic id")
    args = parser.parse_args()

    INBOX.mkdir(exist_ok=True)
    print(f"👀 Folder-watch actief op: {INBOX}")
    print(f"   Drop PDF's in deze folder en ze worden automatisch verwerkt.")
    print(f"   Stoppen: Ctrl+C\n")

    # Verwerk eerst bestaande PDF's in inbox
    existing = list(INBOX.glob("*.pdf"))
    if existing:
        print(f"📁 {len(existing)} bestaande PDF(s) eerst verwerken...")
        for pdf in existing:
            cmd = [sys.executable, str(BASE_DIR / "verwerk.py"), str(pdf)]
            if args.leverancier_hint:
                cmd += ["--leverancier-hint", args.leverancier_hint]
            if args.auto_confirm:
                cmd += ["--auto-confirm"]
            if args.analytic:
                cmd += ["--analytic", str(args.analytic)]
            subprocess.run(cmd)

    # Start observer
    handler = PDFHandler(args)
    observer = Observer()
    observer.schedule(handler, str(INBOX), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\n👋 Folder-watch gestopt.")
    observer.join()


if __name__ == "__main__":
    main()
