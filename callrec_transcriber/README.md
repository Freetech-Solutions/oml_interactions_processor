# Call Recording Transcriber Worker

Worker **Gearman** que recibe la **clave S3** del archivo MP3 de una grabación (`callid`), lo descarga de S3, convierte a WAV, transcribe con varios motores STT y opcionalmente **sumariza** el texto, subiendo transcripción y resumen al mismo bucket S3.

**Flujo:** No requiere volumen compartido con Asterisk; todo el audio se obtiene y se guarda vía S3.

---

## Features

- **Payload mínimo:** solo `callid` (clave S3 del MP3, ej: `20260102/1767378633.10.mp3`)
- **Motores STT:** Faster-Whisper (local, acepta MP3 directamente), OpenAI Whisper, Google Gemini, ElevenLabs
- **Sumarización:** configurable con Gemini, OpenAI, DeepSeek, Llama (Groq/Ollama) y otros compatibles con API OpenAI (resumen de la llamada)
- **Salida en S3:** mismo prefijo que la grabación: `.json`, `.txt` y `.summary.txt`

---

## Variables de entorno

| Variable | Descripción | Default |
|----------|-------------|---------|
| `S3_BUCKET_NAME` | Bucket S3 | *(requerido)* |
| `AWS_ACCESS_KEY_ID` | Key S3 | *(requerido)* |
| `AWS_SECRET_ACCESS_KEY` | Secret S3 | *(requerido)* |
| `S3_ENDPOINT` | Endpoint custom (MinIO, etc.) | *(opcional)* |
| `S3_REGION_NAME` | Región AWS | *(opcional)* |
| `STT_ENGINE` | Motor STT: `local`, `openai`, `gemini`, `elevenlabs` | `local` |
| `SUMMARIZE_ENGINE` | Motor sumarización: `gemini`, `openai`, `deepseek`, `llama` | `gemini` |
| `SUMMARIZE_FALLBACK_ENGINE` | Motor de respaldo si el principal falla (ej. `gemini`) | *(opcional)* |
| `SUMMARIZE_ENABLED` | Activar sumarización por defecto | `true` |
| `STT_API_KEY` | API key OpenAI / Gemini | *(para SaaS)* |
| `GEMINI_API_KEY` | API key Gemini (alternativa a STT_API_KEY) | *(opcional)* |
| `ELEVENLABS_API_KEY` | API key ElevenLabs | *(para motor elevenlabs)* |
| `TASK_NAME` | Nombre de la tarea Gearman | `tel-callrec-transcriber` |
| `GEARMAN_HOST` | Servidor Gearman | `gearman:4730` |

### Por motor

#### Faster-Whisper (local)
| Variable | Descripción | Default |
|----------|-------------|---------|
| `WHISPER_MODEL` | Modelo | `small` |
| `FASTER_WHISPER_DEVICE` | `cpu` o `cuda` | `cpu` |
| `FASTER_WHISPER_COMPUTE_TYPE` | `int8`, `float16`, etc. | `int8` |

#### Sumarización
| Variable | Descripción | Default |
|----------|-------------|---------|
| `SUMMARIZE_MODEL` | Modelo (Gemini: `gemini-1.5-flash`, OpenAI: `gpt-4o-mini`) | según motor |
| `GEMINI_TRANSCRIPTION_MODEL` | Modelo Gemini para transcripción | `gemini-1.5-flash` |
| `OPENAI_WHISPER_MODEL` | Modelo Whisper | `whisper-1` |

#### Proveedores OpenAI-compatibles (DeepSeek, Llama vía Groq/Ollama)
| Variable | Descripción |
|----------|-------------|
| `OPENAI_COMPATIBLE_BASE_URL` | URL base genérica (fallback si no hay vars por proveedor) |
| `OPENAI_COMPATIBLE_API_KEY` | API key genérica |
| `OPENAI_COMPATIBLE_MODEL` | Nombre del modelo genérico |
| `DEEPSEEK_BASE_URL`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` | Para engine `deepseek` |
| `LLAMA_BASE_URL`, `LLAMA_API_KEY`, `LLAMA_MODEL` | Para engine `llama` (Groq/Ollama) |

---

## Payload Gearman

```json
{
  "callid": "20260102/1767378633.10.mp3",
  "engine": "gemini",
  "language": "es",
  "summarize": true,
  "summarizer_engine": "deepseek",
  "summarizer_model": "deepseek-chat"
}
```

- **callid** (requerido): clave S3 completa del archivo MP3 en el bucket.
- **engine**: motor de transcripción (por defecto `STT_ENGINE`).
- **language**: código de idioma (ej: `es`, `en`).
- **summarize**: si se aplica sumarización (por defecto `SUMMARIZE_ENABLED`).
- **summarizer_engine**: motor de sumarización para esta llamada (`gemini`, `openai`, `deepseek`, `llama`). Por defecto `SUMMARIZE_ENGINE`.
- **summarizer_model**: modelo a usar en esta llamada (override por job). Opcional.

---

## Salida en S3

Para `callid` = `20260102/1767378633.10.mp3` se generan:

| Clave S3 | Contenido |
|----------|-----------|
| `20260102/1767378633.10.json` | JSON con meta, texto completo, segmentos y summary |
| `20260102/1767378633.10.txt` | Texto plano de la transcripción |
| `20260102/1767378633.10.summary.txt` | Resumen (si sumarización activa) |

### Estructura del JSON

```json
{
  "meta": {"s3_key": "20260102/1767378633.10.mp3", "engine": "gemini"},
  "text": "texto transcrito completo",
  "segments": [{"start": 0.0, "end": 4.2, "text": "..."}],
  "summary": "resumen generado por LLM"
}
```

---

## Docker

No se monta volumen de grabaciones de Asterisk; el worker solo necesita red (Gearman, S3, APIs).

```yaml
services:
  callrec-transcriber:
    build: .
    environment:
      - STT_ENGINE=gemini
      - S3_BUCKET_NAME=mybucket
      - AWS_ACCESS_KEY_ID=yourkey
      - AWS_SECRET_ACCESS_KEY=yoursecret
      - STT_API_KEY=your-gemini-or-openai-key
      - GEARMAN_HOST=gearman:4730
      - SUMMARIZE_ENABLED=true
```

---

## Arquitectura

```text
Gearman job {"callid": "YYYYMMDD/file.mp3"}
       │
       ▼
┌──────────────────┐
│ Download MP3 S3  │
└────────┬─────────┘
         │
         ├── motor local (faster-whisper) ──► Transcribir MP3 directamente
         │
         ▼
┌──────────────────┐
│ MP3 → WAV (ffmpeg)│  (solo para openai/gemini/elevenlabs)
└────────┬─────────┘
         ▼
┌──────────────────┐
│ Transcribe (STT) │  ← local | openai | gemini | elevenlabs
└────────┬─────────┘
         ▼
┌──────────────────┐
│ Summarize (LLM)  │  ← gemini | openai | deepseek | llama (OpenAI-compatible)
└────────┬─────────┘
         ▼
┌──────────────────┐
│ Upload .json, .txt, .summary.txt to S3 │
└──────────────────┘
```

---

## Migración desde el formato anterior

El worker ya **no** acepta `fileName` ni `dateFileName`. Quien envíe jobs debe usar `callid` con la clave S3 completa del MP3 (por ejemplo, el valor de `archivo_grabacion` en LlamadaResumen: `YYYYMMDD/filename.mp3`).

---

## Licencia

GPLV3. Ver [LICENSE](LICENSE).
