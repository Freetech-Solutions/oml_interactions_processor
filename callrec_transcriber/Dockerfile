# syntax=docker/dockerfile:1

########################################
# 0) Base builder común: deps sistema + venv
########################################
FROM python:3.12-slim AS base-builder

# deps para compilación y audio
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      git build-essential ffmpeg sox && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# venv aislado
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

########################################
# 1) Builder SaaS: sólo deps remotas
########################################
FROM base-builder AS builder-saas

COPY requirements_saas.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements_saas.txt

########################################
# 2) Builder On-Premise: SaaS + Whisper/Triton
########################################
FROM builder-saas AS builder-onprem

# añadimos sólo lo que falta para el motor local
RUN pip install --upgrade pip \
 && pip install --no-cache-dir triton==3.3.1 openai-whisper==20250625

########################################
# 3) Imagen final SaaS
########################################
FROM python:3.12-slim AS saas

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg sox && \
    rm -rf /var/lib/apt/lists/*

# usuario con home válido para cache de 3rd-party libs
RUN addgroup --system appgroup \
 && adduser --system --ingroup appgroup --home /home/omnileads -u 1000 omnileads \
 && mkdir -p /home/omnileads/.cache \
 && chown -R omnileads:appgroup /home/omnileads

# traemos sólo el venv con deps SaaS
COPY --from=builder-saas /opt/venv /opt/venv

WORKDIR /app
COPY src/callrec_transcriber.py .

ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/home/omnileads

USER omnileads
CMD ["python", "callrec_transcriber.py"]

########################################
# 4) Imagen final On-Premise
########################################
FROM python:3.12-slim AS onpremise

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg sox && \
    rm -rf /var/lib/apt/lists/*

RUN addgroup --system appgroup \
 && adduser --system --ingroup appgroup --home /home/omnileads -u 1000 omnileads \
 && mkdir -p /home/omnileads/.cache \
 && chown -R omnileads:appgroup /home/omnileads

# traemos el venv con deps SaaS + Whisper/Triton
COPY --from=builder-onprem /opt/venv /opt/venv

WORKDIR /app
COPY src/callrec_transcriber.py .

ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/home/omnileads

USER omnileads
CMD ["python", "callrec_transcriber.py"]
