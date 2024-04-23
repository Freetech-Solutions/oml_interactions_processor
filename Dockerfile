# Etapa de construcción
FROM python:3.12-slim-bookworm as build

WORKDIR /app
COPY requirements.txt /app/

RUN apt-get update\
    && apt-get install -y\
    build-essential\
    libpq-dev\
    libffi-dev\
    sox        

RUN rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip setuptools \
    && pip install --no-cache-dir -r requirements.txt

# Etapa de ejecución
FROM python:3.12-slim-bookworm as run

COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py /app/
