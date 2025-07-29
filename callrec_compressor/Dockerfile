# Etapa de construcción
FROM python:3.8-slim-bookworm AS build

WORKDIR /app
COPY requirements.txt /app/

RUN apt-get update\
    && apt-get install -y\
    build-essential\
    libpq-dev\
    libffi-dev

RUN rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip setuptools \
    && pip install --no-cache-dir -r requirements.txt

# Etapa de ejecución
FROM python:3.8-slim-bookworm AS run

COPY --from=build /usr/local/lib/python3.8/site-packages /usr/local/lib/python3.8/site-packages
COPY --from=build /usr/local/bin /usr/local/bin

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg sox \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY src/callrec_compressor.py /app/

RUN useradd -M -u 1000 omnileads
RUN chown -R omnileads.omnileads /app

ENTRYPOINT [ "python3", "callrec_compressor.py" ]