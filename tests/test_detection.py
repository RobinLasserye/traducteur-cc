#!/usr/bin/env python3
"""Tests du détecteur double Ctrl+C (sans matériel)."""
import importlib.util
import os
import sys

spec = importlib.util.spec_from_file_location(
    'ctrlcc_daemon',
    os.path.join(os.path.dirname(__file__), '..', 'src', 'ctrlcc_daemon.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
from evdev import ecodes as e  # noqa: E402

CTRL, C, V = e.KEY_LEFTCTRL, e.KEY_C, e.KEY_V


def run(seq):
    """seq = [(code, value, t)] → liste des instants de déclenchement."""
    d = mod.DoubleCtrlC()
    return [t for code, value, t in seq if d.feed(code, value, t)]


cases = [
    ('double Ctrl+C rapide → déclenche',
     [(CTRL, 1, 0), (C, 1, 0.1), (C, 0, 0.15), (C, 1, 0.3), (C, 0, 0.35)],
     [0.3]),
    ('Ctrl+C simple → rien',
     [(CTRL, 1, 0), (C, 1, 0.1), (C, 0, 0.2), (CTRL, 0, 0.3)],
     []),
    ('deux Ctrl+C séparés (relâche Ctrl entre) mais rapides → déclenche',
     [(CTRL, 1, 0), (C, 1, 0.1), (C, 0, 0.15), (CTRL, 0, 0.2),
      (CTRL, 1, 0.3), (C, 1, 0.4), (C, 0, 0.45)],
     [0.4]),
    ('deux Ctrl+C trop espacés (> 0.6 s) → rien',
     [(CTRL, 1, 0), (C, 1, 0.1), (C, 0, 0.15), (C, 1, 1.2), (C, 0, 1.25)],
     []),
    ('C sans Ctrl (frappe normale "cc") → rien',
     [(C, 1, 0.1), (C, 0, 0.15), (C, 1, 0.2), (C, 0, 0.25)],
     []),
    ('triple Ctrl+C → un seul déclenchement puis réarmement',
     [(CTRL, 1, 0), (C, 1, 0.1), (C, 1, 0.3), (C, 1, 0.5)],
     [0.3]),
    ('répétition auto de C (value=2) ignorée',
     [(CTRL, 1, 0), (C, 1, 0.1), (C, 2, 0.15), (C, 2, 0.2)],
     []),
    ('Ctrl droit fonctionne aussi',
     [(e.KEY_RIGHTCTRL, 1, 0), (C, 1, 0.1), (C, 1, 0.3)],
     [0.3]),
]

failed = 0
for name, seq, expected in cases:
    got = run(seq)
    status = 'OK ' if got == expected else 'FAIL'
    if got != expected:
        failed += 1
        print(f'{status} {name} — attendu {expected}, obtenu {got}')
    else:
        print(f'{status} {name}')
sys.exit(1 if failed else 0)
