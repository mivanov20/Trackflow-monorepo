#!/bin/sh
# Entrypoint de desarrollo de la API.
#
# Ningún secreto vive en el repositorio. Si JWT_SECRET no está definido (por ejemplo en un clon
# nuevo sin .env), se genera uno aleatorio solo para esta ejecución del contenedor. Se exporta aquí,
# antes de lanzar uvicorn, así que el recargador (--reload) lo conserva entre reinicios por cambios
# de código. Al recrear el contenedor cambia y las sesiones abiertas dejan de valer.
# Para mantener las sesiones, define JWT_SECRET (32 caracteres o más) en el archivo .env.
set -e

if [ -z "${JWT_SECRET:-}" ]; then
  JWT_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
  export JWT_SECRET
  echo "[api] JWT_SECRET is not set: using a random one for this container run (set it in .env to keep sessions)." >&2
fi

exec "$@"
