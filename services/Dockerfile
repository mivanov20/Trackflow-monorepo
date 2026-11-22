# Imagen de desarrollo de la API FastAPI (uvicorn con --reload).
#
# El código fuente NO se necesita en la imagen para trabajar: docker-compose.yml lo monta desde el
# equipo (bind mount) y uvicorn recarga al guardar. Se copia igualmente para que la imagen arranque sola.
#
# Se construye desde `services/`. La carpeta `packages/shared` (la librería de validación que usa la
# API) está fuera de ese contexto: Compose la aporta como contexto adicional llamado `shared`.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# uv instala las dependencias mucho más rápido que pip.
RUN pip install --no-cache-dir uv

# Misma disposición que el repositorio: `services` es un paquete importable (services.main:app)
# y requirements.txt referencia la librería compartida como `-e ./packages/shared`.
WORKDIR /app

# Librería compartida (solo lo necesario, sin .venv ni pruebas).
COPY --from=shared pyproject.toml /app/packages/shared/pyproject.toml
COPY --from=shared trackflow_shared /app/packages/shared/trackflow_shared

# Pipelines de datos: `services/reporting` importa `data.pipelines` (Compose lo aporta como contexto adicional `pipelines`).
# `data` no necesita __init__.py: Python lo trata como paquete de espacio de nombres.
COPY --from=pipelines . /app/data/pipelines

# Dependencias: esta capa se reutiliza mientras no cambie requirements.txt.
COPY requirements.txt /app/requirements.txt
RUN uv pip install --system -r requirements.txt

COPY . /app/services

# Entrypoint: crea una clave JWT aleatoria de desarrollo si no hay ninguna configurada.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
 && useradd --create-home --uid 1000 app \
 && mkdir -p /data \
 && chown -R app:app /data /app

USER app

EXPOSE 8000

ENTRYPOINT ["sh", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "services.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", \
     "--reload-dir", "/app/services", "--reload-dir", "/app/packages/shared"]
