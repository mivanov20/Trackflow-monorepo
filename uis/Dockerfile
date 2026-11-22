# Imagen de desarrollo de las interfaces: sitio público (website, :3000) y backoffice (:3001)
# en un solo contenedor, ambos con `next dev` y recarga en caliente.
#
# El código fuente NO se copia a la imagen: docker-compose.yml lo monta desde el equipo
# (bind mount), así que los cambios se ven sin reconstruir. La imagen aporta Node y las dependencias.
FROM node:22-alpine

# Un binario que algunas dependencias nativas de Next esperan en Alpine (musl).
RUN apk add --no-cache libc6-compat

ENV NEXT_TELEMETRY_DISABLED=1

# Misma disposición que el repositorio: el backoffice importa ../../../src/... (la carpeta `src/`
# de la raíz), que Compose monta en /repo/src. Por eso las apps viven en /repo/uis/*.
WORKDIR /repo/uis

# Dependencias de cada app por separado. Se copian solo los package.json para que esta capa
# se reutilice mientras no cambien. (Estas dos apps no tienen package-lock.json: se usa npm install.)
COPY website/package.json ./website/
COPY backoffice/package.json ./backoffice/
RUN cd website && npm install --no-audit --no-fund \
 && cd ../backoffice && npm install --no-audit --no-fund

# Script de arranque. Se normalizan los saltos de línea por si el archivo llegó con CRLF.
COPY start.sh ./start.sh

# Las carpetas .next (caché de Next) se montan como volúmenes de Docker. Un volumen nuevo hereda el
# propietario de la carpeta de la imagen, así que se crean aquí y se entregan al usuario `node`;
# si no existieran, el volumen sería de root y `next dev` fallaría con EACCES.
RUN mkdir -p website/.next backoffice/.next \
 && sed -i 's/\r$//' ./start.sh \
 && chown -R node:node /repo

USER node

EXPOSE 3000 3001

# `sh start.sh` (y no ./start.sh) para no depender del bit de ejecución del archivo montado.
CMD ["sh", "/repo/uis/start.sh"]
