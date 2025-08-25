# Callrec Transcriber

Este componente escucha mensajes en RabbitMQ, descarga grabaciones de llamadas desde S3,
las transcribe con Whisper, analiza sentimiento con OpenAI ChatCompletion y almacena
los resultados en una base de datos Postgres.

## Variables de entorno

| Variable              | Descripción                             | Obligatorio |
|-----------------------|-----------------------------------------|-------------|
| GEARMAN_HOST          | Host:puerto del Gearman Job Server      | Sí          |
| AWS_ACCESS_KEY_ID     | AWS Access Key ID                       | Sí          |
| AWS_SECRET_ACCESS_KEY | AWS Secret Access Key                   | Sí          |
| S3_BUCKET_NAME        | Nombre del bucket S3                    | Sí          |
| S3_ENDPOINT           | Endpoint URL de S3 (s3:// o HTTP)       | Sí          |
| OPENAI_API_KEY        | API Key para OpenAI                     | Sí          |
| LOG_LEVEL             | Nivel de logging (INFO, DEBUG, etc.)    | No          |

## Uso con Docker

Construir imagen:
```
docker build -t callrec-transcriber .
```

Ejecutar:
```
docker run -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  -e S3_BUCKET_NAME=... \
  -e S3_ENDPOINT=... \
  -e RABBITMQ_HOST=... \
  -e RABBITMQ_QUEUE=... \
  -e OPENAI_API_KEY=... \
  -e PGHOST=... -e PGDATABASE=... -e PGUSER=... -e PGPASSWORD=... \
  callrec-transcriber
```
