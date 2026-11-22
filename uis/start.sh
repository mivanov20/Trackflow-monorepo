#!/bin/sh
# Arranca las dos aplicaciones Next.js en modo desarrollo dentro de un único contenedor:
#   website    -> puerto 3000 (sitio público)
#   backoffice -> puerto 3001 (panel interno)
# Ambas escuchan en 0.0.0.0 para ser accesibles desde fuera del contenedor.
#
# Si una de las dos se cae, se detiene la otra y el script termina con error:
# así `docker compose` muestra el problema en lugar de dejar medio contenedor funcionando.

set -u
cd "$(dirname "$0")"

WEBSITE_PORT="${WEBSITE_PORT:-3000}"
BACKOFFICE_PORT="${BACKOFFICE_PORT:-3001}"

# Se llama al binario de Next directamente (no con npx) para que cada app sea UN proceso
# y las señales de parada le lleguen sin intermediarios.
(cd website && exec ./node_modules/.bin/next dev -H 0.0.0.0 -p "$WEBSITE_PORT") &
website_pid=$!
(cd backoffice && exec ./node_modules/.bin/next dev -H 0.0.0.0 -p "$BACKOFFICE_PORT") &
backoffice_pid=$!

stop_all() {
  kill "$website_pid" "$backoffice_pid" 2>/dev/null
  wait "$website_pid" "$backoffice_pid" 2>/dev/null
}

# `docker compose stop` / Ctrl+C: parada ordenada de las dos apps.
trap 'stop_all; exit 0' INT TERM

echo "[ui] website on :$WEBSITE_PORT (pid $website_pid), backoffice on :$BACKOFFICE_PORT (pid $backoffice_pid)"

while kill -0 "$website_pid" 2>/dev/null && kill -0 "$backoffice_pid" 2>/dev/null; do
  sleep 2
done

echo "[ui] one of the apps stopped; stopping the other one" >&2
stop_all
exit 1
