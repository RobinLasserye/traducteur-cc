#!/usr/bin/env python3
"""Daemon Ctrl+C+C — déclenche la traduction du texte sélectionné.

Écoute tous les claviers (evdev), détecte deux Ctrl+C rapprochés (< 600 ms),
lit alors le presse-papier (le premier Ctrl+C a déjà copié la sélection) et
ouvre le popup de traduction (ctrlcc-popup.py).

Prérequis : membre du groupe `input` (lecture /dev/input/event*), session
Wayland (wl-paste), popup installé à côté de ce script.
"""
import fcntl
import logging
import os
import selectors
import queue
import threading
import subprocess
import sys
import time

import evdev
from evdev import ecodes as e

DOUBLE_DELAY = 0.6      # délai max entre les deux Ctrl+C
CLIPBOARD_WAIT = 0.30   # laisse l'app cible finir son « copier »
RESCAN_EVERY = 15       # s — reprise des claviers branchés/débranchés
MAX_CHARS = 15000
STATE_DIR = os.path.expanduser('~/.local/state/ctrlcc')
POPUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ctrlcc-popup.py')
# Nos propres périphériques virtuels, à ne jamais écouter (boucle sinon)
IGNORED_NAMES = ('ctrlcc paste', 'type-clipboard')

log = logging.getLogger('ctrlcc')


class DoubleCtrlC:
    """Détecteur : deux appuis sur C avec Ctrl maintenu, en < DOUBLE_DELAY s."""

    def __init__(self, delay=DOUBLE_DELAY):
        self.delay = delay
        self.ctrl = set()      # codes Ctrl actuellement enfoncés
        self.last_c = float('-inf')

    def feed(self, code, value, now):
        """Retourne True si le double Ctrl+C vient d'être complété."""
        if code in (e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL):
            if value == 1:
                self.ctrl.add(code)
            elif value == 0:
                self.ctrl.discard(code)
            return False
        if code == e.KEY_C and value == 1 and self.ctrl:
            if now - self.last_c <= self.delay:
                self.last_c = float('-inf')
                return True
            self.last_c = now
        return False


def is_keyboard(dev):
    if any(n in dev.name.lower() for n in IGNORED_NAMES):
        return False
    keys = dev.capabilities().get(e.EV_KEY, [])
    return e.KEY_C in keys and e.KEY_LEFTCTRL in keys


def scan_keyboards(sel, watched):
    current = set()
    for path in evdev.list_devices():
        current.add(path)
        if path in watched:
            continue
        try:
            dev = evdev.InputDevice(path)
            if is_keyboard(dev):
                sel.register(dev, selectors.EVENT_READ)
                watched[path] = dev
                log.info('clavier suivi : %s (%s)', dev.name, path)
            else:
                dev.close()
        except OSError:
            pass
    for path in [p for p in watched if p not in current]:
        drop_device(sel, watched, path)


def drop_device(sel, watched, path):
    dev = watched.pop(path)
    try:
        sel.unregister(dev)
        dev.close()
    except OSError:
        pass
    log.info('clavier retiré : %s', path)


def get_clipboard():
    try:
        r = subprocess.run(['wl-paste', '--no-newline'], capture_output=True,
                           text=True, timeout=5)
        return r.stdout if r.returncode == 0 else ''
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ''


def notify(message):
    subprocess.run(['notify-send', '-a', 'Traducteur Ctrl+CC', '-i',
                    'accessories-dictionary', 'Traducteur', message], check=False)


def trigger(popup_holder):
    time.sleep(CLIPBOARD_WAIT)
    text = get_clipboard()
    if not text.strip():
        notify('Presse-papier vide — sélectionne du texte avant Ctrl+C+C.')
        return
    if len(text) > MAX_CHARS:
        notify(f'Texte trop long ({len(text)} caractères, max {MAX_CHARS}).')
        return
    prev = popup_holder.get('proc')
    if prev and prev.poll() is None:
        prev.terminate()
    log.info('déclenché — %d caractères', len(text))
    with open(os.path.join(STATE_DIR, 'popup-stderr.log'), 'ab') as stderr:
        proc = subprocess.Popen([sys.executable, POPUP], stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=stderr)
    try:
        proc.stdin.write(text.encode())
        proc.stdin.close()
    except BrokenPipeError:
        log.warning('popup mort avant réception du texte')
    popup_holder['proc'] = proc
    if prev:
        # Récolte le processus remplacé : pas de zombies après des demandes rapides.
        try:
            prev.wait(timeout=2)
        except subprocess.TimeoutExpired:
            prev.kill()
            prev.wait()


class TriggerWorker:
    """Lecture du presse-papier hors boucle evdev, une seule demande en attente."""

    def __init__(self):
        self.pending = queue.Queue(maxsize=1)
        self.popup_holder = {}
        threading.Thread(target=self.run, daemon=True).start()

    def submit(self):
        try:
            self.pending.put_nowait(True)
        except queue.Full:
            pass  # La demande déjà en attente lira la dernière sélection.

    def run(self):
        while True:
            self.pending.get()
            try:
                trigger(self.popup_holder)
            except Exception:
                log.exception('échec déclenchement — écoute clavier maintenue')


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    logging.basicConfig(
        filename=os.path.join(STATE_DIR, 'daemon.log'), level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s')

    # Instance unique
    lock = open(os.path.join(STATE_DIR, 'daemon.lock'), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit('ctrlcc-daemon déjà en cours')
    lock.write(str(os.getpid()))
    lock.flush()

    sel = selectors.DefaultSelector()
    watched = {}
    detector = DoubleCtrlC()
    trigger_worker = TriggerWorker()
    scan_keyboards(sel, watched)
    log.info('démarré — %d clavier(s)', len(watched))
    last_scan = time.monotonic()

    while True:
        for key, _ in sel.select(timeout=RESCAN_EVERY):
            dev = key.fileobj
            try:
                for ev in dev.read():
                    if ev.type != e.EV_KEY:
                        continue
                    if detector.feed(ev.code, ev.value, time.monotonic()):
                        trigger_worker.submit()
            except OSError:
                drop_device(sel, watched, dev.path)
        if time.monotonic() - last_scan >= RESCAN_EVERY:
            scan_keyboards(sel, watched)
            last_scan = time.monotonic()


if __name__ == '__main__':
    main()
