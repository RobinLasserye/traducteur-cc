# Traducteur Ctrl+C+C

Réplique locale et open source du raccourci **Ctrl+C+C de DeepL**, pour Linux
(Wayland/KDE) : sélectionnez du texte n'importe où, appuyez deux fois
rapidement sur `Ctrl+C`, et une carte flottante affiche la traduction —
générée **100 % en local** par un modèle Ollama, sans envoyer votre texte à un
service en ligne.

## Fonctionnement

- **Double Ctrl+C** (< 600 ms) → le daemon lit le presse-papier (le premier
  `Ctrl+C` a déjà copié votre sélection) et ouvre le popup.
- **Détection de langue automatique** : texte en langue A → traduit vers la
  langue B ; texte dans toute autre langue → traduit vers A
  (défaut : français ↔ anglais).
- Le popup **ne vole pas le focus** : votre application reste active.
  - **Remplacer** — colle la traduction à la place de la sélection
    (injection `Ctrl+V` via uinput), pour le texte que vous écrivez ;
  - **Copier** — met la traduction dans le presse-papier, pour le texte
    affiché non modifiable ;
  - **⚙** — paramètres : les deux langues et le modèle Ollama ;
  - **✕** — fermer (auto-fermeture 2 min après le résultat).

## Prérequis

- Session **Wayland** avec KDE Plasma (testé sur Fedora 43) ;
- `wl-clipboard`, `python3-evdev`, PyQt6, `notify-send` ;
- [Ollama](https://ollama.com) en local avec un modèle instruct
  (défaut : `qwen3:8b`) ;
- votre utilisateur membre du groupe `input` (lecture des claviers) et accès
  en écriture à `/dev/uinput` (pour le bouton Remplacer).

## Installation

```bash
git clone https://github.com/RobinLasserye/traducteur-cc.git
cd traducteur-cc
./install.sh
```

Le script installe les deux binaires dans `~/.local/bin/`, crée la config
par défaut `~/.config/ctrlcc/config.json`, active le démarrage automatique
(`~/.config/autostart/ctrlcc.desktop`) et lance le daemon immédiatement.

## Configuration

`~/.config/ctrlcc/config.json` (modifiable aussi via le bouton ⚙ du popup) :

```json
{
  "lang_a": "français",
  "lang_b": "anglais",
  "model": "qwen3:8b"
}
```

## Architecture

| Composant | Rôle |
|---|---|
| `src/ctrlcc_daemon.py` | Écoute les claviers (evdev), détecte le double `Ctrl+C`, lit le presse-papier, lance le popup. Gère le branchement/débranchement de claviers à chaud. |
| `src/ctrlcc-popup.py` | Carte PyQt6 sans vol de focus. Détection de langue puis traduction progressive (deux appels Ollama), injection `Ctrl+V` par clavier virtuel uinput préparé à l'avance. |
| `tests/test_detection.py` | 8 cas sur le détecteur de double `Ctrl+C` (frappe normale, répétition auto, Ctrl droit, délais…). |

Journaux : `~/.local/state/ctrlcc/daemon.log`, `popup.log` (durées des appels
et du premier texte) et `popup-stderr.log` (erreurs Qt et lancement).

### Vérification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_detection.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_stability.py
```

Les tests de stabilité utilisent Qt offscreen, sans clavier ni Ollama.
Ils vérifient notamment la fermeture pendant une requête, les changements de
paramètres, les résultats obsolètes et les réponses Ollama interrompues.

### Stabilité et réactivité

- Le presse-papier est lu dans un worker avec une seule demande en attente :
  un logiciel lent à répondre ne bloque plus l'écoute du clavier.
- Le texte traduit apparaît progressivement ; Copier et Remplacer restent
  désactivés jusqu'à la réception d'une réponse complète.
- Les workers réseau peuvent être abandonnés à la fermeture sans détruire un
  QThread actif. Une ancienne requête ne peut pas écraser le résultat suivant.
- La fermeture automatique démarre après le résultat ou l'erreur, afin de ne
  pas interrompre une traduction longue.
- Le modèle et le sens de traduction restent configurables comme auparavant.

Si tout devient lent, vérifier `ollama ps` : le modèle doit utiliser le GPU
lorsqu'il est disponible. Une exécution CPU peut multiplier la latence ; les
journaux Ollama permettent de distinguer ce cas d'un problème de popup.

### Choix techniques notables

- **Pas de prompt conditionnel** pour le sens de traduction : « si français →
  anglais, sinon → français » fait recopier le texte tel quel par les petits
  modèles. La langue est détectée par un premier mini-appel, puis la consigne
  de traduction est directe (« traduis en X »).
- Le clavier virtuel uinput du bouton Remplacer est créé dès l'ouverture du
  popup (KWin met ~1 s à détecter un nouveau périphérique) pour que le
  collage soit instantané au clic.
- Le daemon ignore ses propres périphériques virtuels pour éviter toute
  boucle d'événements.

## Licence

GPL-3.0 — voir [LICENSE](LICENSE). © 2026 Robin Lasserye.
