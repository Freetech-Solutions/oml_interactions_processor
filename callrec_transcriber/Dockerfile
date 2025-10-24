# syntax=docker/dockerfile:1

########################################
# 1) Base builder: system deps + venv + pip install
########################################
FROM python:3.12-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive

# deps para compilación y audio (build-essential solo en builder)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      git build-essential ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Crear venv ANTES de instalar dependencias
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Actualizar pip/packaging dentro del venv
RUN python -m pip install --upgrade pip setuptools wheel

# Copiar requirements y pip install dentro del venv
COPY requirements.txt .
# Prefer binary wheels but allow fallback if binary missing (safer than --only-binary)
RUN python -m pip install --no-cache-dir --prefer-binary -r requirements.txt

# Copiar código para que pueda aprovechar cache de capas si cambia sólo el código
COPY src/ /app

########################################
# 2) Imagen final (runtime)
########################################
FROM python:3.12-slim AS run

ENV DEBIAN_FRONTEND=noninteractive

# Runtime deps: solo lo necesario (no build-essential)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Crear usuario no-root
RUN addgroup --system appgroup \
 && adduser --system --ingroup appgroup --home /home/omnileads -u 1000 omnileads \
 && mkdir -p /home/omnileads/.cache \
 && chown -R omnileads:appgroup /home/omnileads

# Traer el venv con todas las deps instaladas
COPY --from=builder /opt/venv /opt/venv
RUN chown -R omnileads:appgroup /opt/venv

# App
WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/home/omnileads \
    HF_HOME=/opt/models \
    FASTER_WHISPER_DEVICE=cpu \
    FASTER_WHISPER_COMPUTE_TYPE=int8 \
    WHISPER_MODEL=small

USER omnileads

# (Opcional) Healthcheck sencillo
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "print('ok')" || exit 1

CMD ["python", "callrec_transcriber.py"]
