# syntax=docker/dockerfile:1

########################################
# 1) Base builder: system deps + venv + pip install
########################################
FROM python:3.12-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive

# deps para compilación y audio (build-essential solo en builder)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      git build-essential ffmpeg wget ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Crear venv ANTES de instalar dependencias
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Actualizar pip/packaging dentro del venv
RUN python -m pip install --upgrade pip setuptools wheel

# Copiar requirements y pip install dentro del venv
COPY requirements.txt .
# Prefer binary wheels but allow fallback if binary missing
RUN python -m pip install --no-cache-dir --prefer-binary -r requirements.txt

# Copiar código (para aprovechar cache de capas)
COPY src/ /app

########################################
# 2) Imagen final (runtime)
########################################
FROM python:3.12-slim AS run

ENV DEBIAN_FRONTEND=noninteractive

# Runtime deps: solo lo necesario (no build-essential)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg wget ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# copiar venv y app desde builder
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

# instanciar usuario/grupo sistema (sin forzar UID)
RUN addgroup --system appgroup \
 && adduser --system --ingroup appgroup --home /home/omnileads --no-create-home omnileads \
 && mkdir -p /home/omnileads/.cache

# asegurar que el venv y el app dir sean legibles por omnileads
RUN chown -R omnileads:appgroup /opt/venv /app /home/omnileads

# crear el directorio HF_HOME (model cache) — el entrypoint hará chown si es necesario
RUN mkdir -p /opt/models /opt/models/hub

# copiar entrypoint y hacerlo ejecutable
COPY src/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# instalar gosu para hacer exec como usuario no-root de forma segura
RUN set -eux; \
    dpkgArch="$(dpkg --print-architecture | awk -F- '{ print $NF }')"; \
    wget -O /usr/local/bin/gosu "https://github.com/tianon/gosu/releases/download/1.16/gosu-${dpkgArch}"; \
    chmod +x /usr/local/bin/gosu; \
    gosu --version || true

# Env defaults
ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/home/omnileads \
    HF_HOME=/opt/models \
    FASTER_WHISPER_DEVICE=cpu \
    FASTER_WHISPER_COMPUTE_TYPE=int8 \
    WHISPER_MODEL=small \
    OMNILEADS_USER=omnileads \
    OMNILEADS_GROUP=appgroup

# ENTRYPOINT prepara (como root) y luego lanza el CMD como omnileads
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "callrec_transcriber.py"]
