#!/usr/bin/env python3
"""Popup de traduction Ctrl+C+C (PyQt6, Wayland/KDE).

Lit le texte sur stdin, le traduit via Ollama local (détection de langue par
le modèle : langue A ↔ langue B, configurables), l'affiche dans une carte
flottante qui NE PREND PAS le focus (l'application cible reste active), avec :
  Remplacer — copie la traduction puis injecte Ctrl+V (uinput) dans l'app cible
  Copier    — copie la traduction dans le presse-papier
  ⚙         — paramètres (langues + modèle)
"""
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request

from PyQt6.QtCore import Qt, QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget)

CONFIG_PATH = os.path.expanduser('~/.config/ctrlcc/config.json')
STATE_DIR = os.path.expanduser('~/.local/state/ctrlcc')
DEFAULTS = {'lang_a': 'français', 'lang_b': 'anglais', 'model': 'qwen3:8b'}
LANGUES = ['français', 'anglais', 'espagnol', 'allemand', 'italien',
           'portugais', 'néerlandais', 'japonais', 'chinois', 'russe', 'arabe']
OLLAMA = 'http://127.0.0.1:11434'
AUTOCLOSE_MS = 120_000

log = logging.getLogger('ctrlcc-popup')


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return {**DEFAULTS, **json.load(f)}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def wl_copy(text):
    subprocess.run(['wl-copy'], input=text.encode(), check=False)


def ollama_models():
    try:
        with urllib.request.urlopen(f'{OLLAMA}/api/tags', timeout=4) as r:
            return [m['name'] for m in json.load(r)['models']]
    except Exception:
        return []


def ollama_chat(model, system, user, timeout=180):
    payload = json.dumps({
        'model': model, 'stream': False, 'think': False,
        'options': {'temperature': 0.2},
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
    }).encode()
    req = urllib.request.Request(f'{OLLAMA}/api/chat', data=payload,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)['message']['content'].strip()


# Le prompt conditionnel (« si français → anglais, sinon → français ») fait
# recopier tel quel les textes non-A par qwen3:8b : la détection doit donc
# être un appel séparé, suivi d'une consigne de traduction sans conditionnel.
LANG_ALIASES = {
    'french': 'français', 'english': 'anglais', 'spanish': 'espagnol',
    'german': 'allemand', 'italian': 'italien', 'portuguese': 'portugais',
    'dutch': 'néerlandais', 'japanese': 'japonais', 'chinese': 'chinois',
    'russian': 'russe', 'arabic': 'arabe',
}


def normalize_lang(name):
    word = name.strip().lower().strip('.,!«»" \n').split()[0] if name.strip() else ''
    return LANG_ALIASES.get(word, word)


def detect_language(model, text):
    answer = ollama_chat(
        model,
        'Identifie la langue principale du texte fourni. Réponds UNIQUEMENT '
        'par le nom de cette langue en français, en minuscules, en un seul '
        'mot. Exemples de réponses : anglais, français, espagnol.',
        text[:600], timeout=60)
    return normalize_lang(answer)


def pick_target(detected, cfg):
    lang_a = normalize_lang(cfg['lang_a'])
    if detected and (detected == lang_a or detected.startswith(lang_a[:6])):
        return cfg['lang_b']
    return cfg['lang_a']


def translate(text, cfg):
    """Retourne (langue détectée, langue cible, traduction)."""
    detected = detect_language(cfg['model'], text)
    target = pick_target(detected, cfg)
    system = (
        f'Tu es un traducteur professionnel. Traduis le texte de '
        f'l\'utilisateur en {target}. Préserve la mise en forme et les '
        'retours à la ligne. Réponds UNIQUEMENT avec la traduction, sans '
        'explication ni commentaire.')
    return detected, target, ollama_chat(cfg['model'], system, text)


class Translator(QObject):
    done = pyqtSignal(str, str)   # traduction, "détectée → cible"
    failed = pyqtSignal(str)

    def __init__(self, text, cfg):
        super().__init__()
        self.text, self.cfg = text, cfg

    def run(self):
        try:
            detected, target, out = translate(self.text, self.cfg)
            log.info('traduction OK %s → %s (%d → %d caractères)',
                     detected, target, len(self.text), len(out))
            self.done.emit(out, f'{detected} → {normalize_lang(target)}')
        except Exception as exc:
            log.error('échec traduction : %s', exc)
            self.failed.emit(f'Échec de la traduction : {exc}\n'
                             'Ollama est-il démarré ? (systemctl status ollama)')


class PasteInjector(QObject):
    """Clavier virtuel uinput prêt à l'avance pour un Ctrl+V instantané."""

    def __init__(self):
        super().__init__()
        self.ui = None

    def prepare(self):
        try:
            from evdev import UInput, ecodes as e
            self.ui = UInput({e.EV_KEY: [e.KEY_LEFTCTRL, e.KEY_V]},
                             name='ctrlcc paste kbd')
            time.sleep(1.0)  # laisse KWin détecter le clavier
        except Exception as exc:
            log.error('uinput indisponible : %s', exc)

    def paste(self):
        if not self.ui:
            return False
        from evdev import ecodes as e
        for code, value in ((e.KEY_LEFTCTRL, 1), (e.KEY_V, 1),
                            (e.KEY_V, 0), (e.KEY_LEFTCTRL, 0)):
            self.ui.write(e.EV_KEY, code, value)
            self.ui.syn()
            time.sleep(0.02)
        return True

    def close(self):
        if self.ui:
            self.ui.close()


class SettingsDialog(QDialog):
    def __init__(self, cfg):
        super().__init__()
        self.setWindowTitle('Traducteur Ctrl+CC — Paramètres')
        form = QFormLayout(self)
        self.lang_a = QComboBox(editable=True)
        self.lang_b = QComboBox(editable=True)
        for combo, current in ((self.lang_a, cfg['lang_a']),
                               (self.lang_b, cfg['lang_b'])):
            combo.addItems(LANGUES)
            combo.setCurrentText(current)
        self.model = QComboBox(editable=True)
        self.model.addItems(ollama_models() or [cfg['model']])
        self.model.setCurrentText(cfg['model'])
        form.addRow('Langue A (cible par défaut) :', self.lang_a)
        form.addRow('Langue B (si texte en A) :', self.lang_b)
        form.addRow('Modèle Ollama :', self.model)
        info = QLabel('Texte en langue A → traduit vers B.\n'
                      'Texte dans toute autre langue → traduit vers A.')
        info.setStyleSheet('color: #888;')
        form.addRow(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return {'lang_a': self.lang_a.currentText().strip(),
                'lang_b': self.lang_b.currentText().strip(),
                'model': self.model.currentText().strip()}


class Popup(QWidget):
    def __init__(self, text):
        super().__init__()
        self.source = text
        self.translation = None
        self.cfg = load_config()
        self.injector = PasteInjector()
        self.build_ui()
        self.start_translation()
        QTimer.singleShot(AUTOCLOSE_MS, QApplication.quit)
        # Prépare le clavier virtuel pendant que la traduction tourne
        self.inj_thread = QThread()
        self.injector.moveToThread(self.inj_thread)
        self.inj_thread.started.connect(self.injector.prepare)
        self.inj_thread.start()

    def build_ui(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet('''
            QWidget { background: #1e1e26; color: #e8e8ef;
                      font-size: 13px; }
            QFrame#card { border: 1px solid #3a3a48; border-radius: 10px; }
            QTextEdit { background: #262630; border: 1px solid #3a3a48;
                        border-radius: 6px; padding: 6px; }
            QPushButton { background: #33334a; border: none; padding: 7px 14px;
                          border-radius: 6px; }
            QPushButton:hover { background: #45456a; }
            QPushButton#replace { background: #2f6b46; font-weight: bold; }
            QPushButton#replace:hover { background: #3a8757; }
        ''')
        card = QFrame(self, objectName='card')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)

        self.title = QLabel('🌐 Traducteur Ctrl+C+C')
        self.title.setFont(QFont('', 11, QFont.Weight.Bold))
        title = self.title
        src = QLabel(self.source[:220] + ('…' if len(self.source) > 220 else ''))
        src.setWordWrap(True)
        src.setStyleSheet('color: #9a9ab0;')
        self.result = QTextEdit(readOnly=True)
        self.result.setPlainText('⏳ Traduction en cours…')
        self.result.setMinimumSize(440, 120)

        btns = QHBoxLayout()
        gear = QPushButton('⚙')
        gear.setFixedWidth(38)
        gear.clicked.connect(self.open_settings)
        self.copy_btn = QPushButton('Copier')
        self.copy_btn.clicked.connect(self.do_copy)
        self.replace_btn = QPushButton('Remplacer', objectName='replace')
        self.replace_btn.clicked.connect(self.do_replace)
        close = QPushButton('✕')
        close.setFixedWidth(38)
        close.clicked.connect(QApplication.quit)
        for b in (gear, self.copy_btn, self.replace_btn, close):
            btns.addWidget(b)
        for w in (self.copy_btn, self.replace_btn):
            w.setEnabled(False)

        for w in (title, src, self.result):
            lay.addWidget(w)
        lay.addLayout(btns)

        geo = QApplication.primaryScreen().availableGeometry()
        self.adjustSize()
        self.move(geo.center().x() - self.width() // 2,
                  geo.y() + int(geo.height() * 0.12))

    def start_translation(self):
        self.worker = Translator(self.source, self.cfg)
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.done.connect(self.show_result)
        self.worker.failed.connect(self.show_error)
        self.thread.start()

    def show_result(self, text, direction):
        self.translation = text
        self.result.setPlainText(text)
        self.title.setText(f'🌐 Traducteur Ctrl+C+C — {direction}')
        self.copy_btn.setEnabled(True)
        self.replace_btn.setEnabled(True)

    def show_error(self, message):
        self.result.setPlainText('⚠ ' + message)

    def do_copy(self):
        wl_copy(self.translation)
        QApplication.quit()

    def do_replace(self):
        wl_copy(self.translation)
        self.hide()
        # petit délai : le presse-papier doit être servi avant le Ctrl+V
        QTimer.singleShot(250, self._inject)

    def _inject(self):
        ok = self.injector.paste()
        if not ok:
            subprocess.run(['notify-send', '-a', 'Traducteur Ctrl+CC',
                            'Traducteur', 'Injection Ctrl+V impossible — la '
                            'traduction est dans le presse-papier, colle-la '
                            'manuellement.'], check=False)
        QTimer.singleShot(300, QApplication.quit)

    def open_settings(self):
        dlg = SettingsDialog(self.cfg)
        if dlg.exec():
            self.cfg = {**self.cfg, **dlg.values()}
            save_config(self.cfg)
            self.result.setPlainText('⏳ Retraduction avec les nouveaux '
                                     'paramètres…')
            self.copy_btn.setEnabled(False)
            self.replace_btn.setEnabled(False)
            self.start_translation()

    def closeEvent(self, event):
        self.injector.close()
        event.accept()


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    logging.basicConfig(
        filename=os.path.join(STATE_DIR, 'popup.log'), level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s')
    text = sys.stdin.read()
    if not text.strip():
        sys.exit(0)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    popup = Popup(text)
    popup.show()
    log.info('popup affiché (%d caractères)', len(text))
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
