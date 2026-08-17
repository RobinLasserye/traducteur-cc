#!/usr/bin/env bash
# Installe le Traducteur Ctrl+C+C : binaires, config par défaut, autostart.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/src" && pwd)"
BIN="$HOME/.local/bin"

install -Dm755 "$SRC/ctrlcc_daemon.py" "$BIN/ctrlcc-daemon.py"
install -Dm755 "$SRC/ctrlcc-popup.py" "$BIN/ctrlcc-popup.py"

# Config par défaut (ne pas écraser une config existante)
CFG="$HOME/.config/ctrlcc/config.json"
if [[ ! -f "$CFG" ]]; then
    mkdir -p "$(dirname "$CFG")"
    cat > "$CFG" <<'EOF'
{
  "lang_a": "français",
  "lang_b": "anglais",
  "model": "qwen3:8b"
}
EOF
fi

# Autostart à l'ouverture de session (même convention que SuperWhisper)
install -Dm644 /dev/stdin "$HOME/.config/autostart/ctrlcc.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Traducteur Ctrl+C+C
Comment=Double Ctrl+C = traduction du texte sélectionné (Ollama local)
Exec=$BIN/ctrlcc-daemon.py
Icon=accessories-dictionary
X-KDE-autostart-after=panel
EOF

# (Re)démarre le daemon
pkill -f 'ctrlcc-daemon.py' 2>/dev/null && sleep 1 || true
setsid "$BIN/ctrlcc-daemon.py" >/dev/null 2>&1 < /dev/null &
sleep 1
pgrep -f 'ctrlcc-daemon.py' >/dev/null && echo "✓ daemon lancé" || { echo "✗ daemon KO"; exit 1; }
echo "✓ Traducteur Ctrl+C+C installé (autostart actif)"
