# Call Recording Transcriber Worker

A Python-based **Gearman worker** that transcribes **Asterisk call recordings (WAV)** into text using multiple **Speech-to-Text (STT)** engines, such as:

- **Faster-Whisper (local)**
- **OpenAI Whisper API (SaaS)**
- **Google Gemini (SaaS)**
- **Google Cloud Speech-to-Text (SaaS)**

Each processed call produces both a **per-channel transcription** and a **consolidated JSON file** with timestamps, which are uploaded to an **S3-compatible storage**.

---

## 🧩 Features

- Supports **multiple STT engines** (local and cloud-based)
- Optional **silence trimming** using FFmpeg
- **Segmented transcription output** (with timestamps and channel separation)
- **Uploads results to S3** (both `.txt` and `.json`)
- Automatic cleanup of temporary and processed files
- Can override engine or language per job dynamically

---

## ⚙️ Environment Variables

| Variable | Description | Default |
|-----------|--------------|----------|
| `STT_ENGINE` | Transcription engine (`local`, `openai`, `gemini`, `gcp`) | `local` |
| `TASK_NAME` | Gearman task name | `tel-callrec-transcriber` |
| `GEARMAN_HOST` | Gearman server and port | `gearman:4730` |
| `ASTERISK_MONITOR_PATH` | Path to Asterisk recordings | `/var/spool/asterisk/monitor` |
| `S3_BUCKET_NAME` | Target S3 bucket | *(required)* |
| `AWS_ACCESS_KEY_ID` | AWS/S3 key | *(required)* |
| `AWS_SECRET_ACCESS_KEY` | AWS/S3 secret | *(required)* |
| `S3_ENDPOINT` | Custom endpoint (for MinIO, etc.) | *(optional)* |
| `S3_REGION_NAME` | AWS region name | *(optional)* |
| `USE_FFMPEG_TRIM` | Enable silence trimming | `false` |
| `TRIM_STOP_DURATION` | Minimum silence duration (sec) | `0.5` |
| `TRIM_THRESHOLD_DB` | Silence threshold (dB) | `-50` |
| `STT_API_KEY` | API key for OpenAI/Gemini engines | *(required for SaaS engines)* |

### Engine-specific variables

#### Faster-Whisper (local)
| Variable | Description | Default |
|-----------|--------------|----------|
| `WHISPER_MODEL` | Model name | `small` |
| `FASTER_WHISPER_DEVICE` | `cpu` or `cuda` | `cpu` |
| `FASTER_WHISPER_COMPUTE_TYPE` | Precision type (`int8`, `float16`, etc.) | `int8` |
| `FW_VAD_FILTER` | Apply VAD filter | `true` |

#### Google Cloud Speech
| Variable | Description | Default |
|-----------|--------------|----------|
| `GCP_SPEECH_MODEL` | Recognition model (`phone_call`, `default`, `latest_long`) | `phone_call` |

---

## 🧠 Job Payload (Gearman Task)

The worker expects a **JSON payload** with the following structure:

```json
{
  "fileName": "CALL-12345",
  "dateFileName": "2025-10-23",
  "language": "es"
}
````

### Required fields

* `fileName`: Base name of the call recording (without suffix `-Rx` or `-Tx`)
* `dateFileName`: Folder name (as stored by Asterisk)
* `language`: Optional language code (e.g., `"es"`, `"en"`)

---

## 🧾 Output Files

Each transcription produces:

1. **Two text files** (one per channel):

   * `CALL-12345-Rx.txt`
   * `CALL-12345-Tx.txt`

2. **A JSON file** consolidating both channels:

   * `CALL-12345.json`

### JSON Structure Example

```json
{
  "base_name": "CALL-12345",
  "date_folder": "2025-10-23",
  "engine": "openai",
  "language": "es",
  "segments": [
    {"start": 0.0, "end": 4.2, "text": "Hola, buenos días", "channel": "Rx"},
    {"start": 4.3, "end": 7.1, "text": "Hola, ¿con quién hablo?", "channel": "Tx"}
  ],
  "text_by_channel": {
    "Rx": "Hola, buenos días",
    "Tx": "Hola, ¿con quién hablo?"
  }
}
```

All files are uploaded to:

```
s3://<S3_BUCKET_NAME>/<dateFileName>/<fileName>.{json|txt}
```

---

## 🧪 Docker Setup

You can easily deploy this worker using Docker.

### Run with Docker Compose

```yaml
version: "3.8"

services:
  callrec-transcriber:
    build: .
    environment:
      - STT_ENGINE=local
      - S3_BUCKET_NAME=mybucket
      - AWS_ACCESS_KEY_ID=yourkey
      - AWS_SECRET_ACCESS_KEY=yoursecret
      - GEARMAN_HOST=gearman:4730
      - ASTERISK_MONITOR_PATH=/recordings
    volumes:
      - /var/spool/asterisk/monitor:/recordings
```

---

## 🧩 Architecture Overview

```text
┌──────────────────────────────┐
│     Asterisk PBX Server      │
│   (Records WAV Files)        │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Gearman Queue (task)         │
│ {"fileName": "...", ...}     │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Transcriber Worker           │
│ - Loads engine (local/API)   │
│ - Optional silence trimming  │
│ - Creates .txt + .json       │
│ - Uploads to S3              │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│       S3 Storage Bucket      │
│   (Transcriptions archived)  │
└──────────────────────────────┘
```

---

## 🧑‍💻 Developer Notes

* You can dynamically override the STT engine via the job payload.
* Use `USE_FFMPEG_TRIM=true` for recordings with long silences.
* The worker deletes the processed WAV files automatically.
* For cloud engines, ensure network access and correct API credentials.

---

## 🪪 License

This project is licensed under the **GPLV3 License**.
See [LICENSE](LICENSE) for details.

---

## 🏗️ Author

**Fabian Pignataro**

```
