#!/usr/bin/env bash
set -euo pipefail
APP_DIR="/var/app/current"
cd "$APP_DIR"

# Installe les dépendances si c'est un premier déploiement
if [ ! -d "node_modules" ]; then
  npm install --no-audit --no-fund || true
fi

# (Re)démarre l'app si rien n'écoute sur $PORT (8080 par défaut)
PORT="${PORT:-8080}"
if ! (echo > /dev/tcp/127.0.0.1/${PORT}) >/dev/null 2>&1; then
  nohup npm start >/var/log/web.stdout.log 2>/var/log/web.stderr.log &
fi
