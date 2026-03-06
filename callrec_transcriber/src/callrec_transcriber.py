#!/usr/bin/env python3
"""
Worker Gearman S3-first: recibe callid (clave S3 del MP3), descarga, opcionalmente convierte a WAV,
transcribe (faster-whisper acepta MP3; OpenAI/Gemini/ElevenLabs usan WAV), sumariza y sube resultados a S3.

El sistema de prompts de sumarización es dinámico: el payload puede indicar prompt_type (default,
sales, support, collections) o enviar custom_prompt para inyección directa. Ver PromptLibrary y
_resolve_prompt_instructions para añadir nuevos tipos de análisis.
"""
import os
import re
import sys
import json
import logging
import tempfile
import subprocess
import concurrent.futures
import gearman
import boto3
import requests
import httpx
from string import Template
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_settings")
from status_updates import SpeechAnalysisStatusUpdater

# --- Modelos Pydantic (validación de respuestas IA y payload) ---


class TranscriptionSegment(BaseModel):
    """Un segmento de la transcripción con timestamps."""
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)
    text: str


class TranscriptionResult(BaseModel):
    """Estructura esperada del resultado de cualquier motor de transcripción."""
    text: str
    segments: List[TranscriptionSegment]


class SummaryResult(BaseModel):
    """Validación de la salida de cualquier motor de sumarización antes de subir a S3."""
    content: str


class TranscriptionJobPayload(BaseModel):
    """
    Payload del job Gearman para transcripción.

    prompt_type: clave del perfil en PromptLibrary (default, sales, support, collections, etc.).
                 Si no existe, se usa 'default'.
    custom_prompt: si se envía, tiene prioridad sobre prompt_type (inyección directa para pruebas).
    meta: metadatos para variables en templates (ej. agent_name, campaign_name); se aplican
          con string.Template al texto de instrucciones del resumen.
    """
    callid: str
    engine: Optional[str] = None
    language: Optional[str] = None
    summarize: Optional[bool] = None
    summarizer_engine: Optional[str] = None
    summarizer_model: Optional[str] = None
    prompt_type: Optional[str] = "default"
    custom_prompt: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


# --- PromptLibrary: perfiles de análisis por tipo de llamada ---


class PromptLibrary:
    """
    Librería de templates de prompts para sumarización según el tipo de análisis.

    Los templates son strings de instrucciones que se concatenan con la transcripción.
    Soporta variables opcionales vía string.Template (ej. $agent_name, $campaign_name),
    rellenadas desde payload.meta.

    Variables opcionales en templates (desde payload.meta): $agent_name, $campaign_name, etc.
    Si meta no tiene una clave, safe_substitute deja el placeholder sin sustituir.

    Cómo añadir un nuevo tipo de prompt:
    1. Añadir una entrada en _TEMPLATES con clave y texto del template.
    2. Los clientes podrán enviar ese valor en prompt_type en el payload.
    3. Opcionalmente usar placeholders $var en el template; si meta contiene
       esa clave, se sustituirá (safe_substitute deja sin sustituir si falta).
    """
    _TEMPLATES: Dict[str, str] = {
        "default": (
            "Resume la siguiente transcripción de una llamada en pocos párrafos. "
            "Incluye puntos clave, acuerdos y acciones. Lenguaje natural y conciso."
        ),
        "sales": (
            "Analiza esta transcripción de una llamada de ventas. Resume en pocos párrafos "
            "enfocándote en: oportunidades de cierre detectadas, objeciones del cliente "
            "y productos o servicios por los que mostró interés."
        ),
        "support": (
            "Analiza esta transcripción de una llamada de soporte. Resume en pocos párrafos "
            "enfocándote en: la solución técnica brindada, el nivel de satisfacción del cliente "
            "y si el ticket requiere escalación o seguimiento."
        ),
        "collections": (
            "Analiza esta transcripción de una llamada de cobranzas. Resume en pocos párrafos "
            "enfocándote en: compromisos de pago acordados, fechas concretas mencionadas "
            "y la actitud del deudor (disposición a pagar, excusas, etc.)."
        ),
    }

    @classmethod
    def get(cls, prompt_type: str) -> str:
        """
        Devuelve el template de instrucciones para el tipo dado.
        Si prompt_type no existe en la librería, devuelve el de 'default'.
        """
        key = (prompt_type or "default").strip().lower()
        return cls._TEMPLATES.get(key, cls._TEMPLATES["default"])

    @classmethod
    def register(cls, key: str, template: str) -> None:
        """Registra o actualiza un template por clave (extensibilidad)."""
        cls._TEMPLATES[key.strip().lower()] = template


# Cláusula de Seguridad y Redacción: cumplimiento de políticas de privacidad y seguridad
# (GDPR/PCI-DSS). El resumen nunca debe incluir PII sensible; es mejor omitir un dato
# que exponer información sensible (p. ej. una tarjeta de crédito).
PII_REDACTION_CLAUSE = (
    "\n\n[CLÁUSULA DE SEGURIDAD Y REDACCIÓN - OBLIGATORIO]\n"
    "El resumen debe cumplir políticas de privacidad y seguridad (GDPR/PCI-DSS). "
    "NUNCA incluyas información de identificación personal (PII) sensible. "
    "Aplica las siguientes redacciones en todo el resumen:\n"
    "- Números de tarjeta de crédito o débito → reemplaza por [CARD_REDACTED].\n"
    "- Contraseñas o PINs → reemplaza por [PASSWORD_REDACTED].\n"
    "- Documentos de identidad (DNI, SSN, etc.) → reemplaza por [ID_REDACTED].\n"
    "- Nombres propios y direcciones que no sean estrictamente necesarios para el resumen "
    "→ anonimiza usando [CLIENT_NAME] o [ADDRESS].\n"
    "Bajo ninguna circunstancia incluyas datos sensibles en los puntos clave ni en los acuerdos."
)


def _resolve_prompt_instructions(payload: TranscriptionJobPayload) -> str:
    """
    Resuelve el texto de instrucciones para el resumen: custom_prompt tiene prioridad
    sobre prompt_type; aplica sustitución de variables desde payload.meta.
    Siempre concatena la cláusula de redacción PII (GDPR/PCI-DSS) para todos los tipos.
    """
    if payload.custom_prompt and payload.custom_prompt.strip():
        instructions_template = payload.custom_prompt.strip()
    else:
        instructions_template = PromptLibrary.get(payload.prompt_type or "default")
    meta = payload.meta or {}
    mapping = {k: (v if v is not None else "") for k, v in meta.items()}
    base = Template(instructions_template).safe_substitute(mapping)
    return base + PII_REDACTION_CLAUSE


def redact_summary_pii(summary: str) -> str:
    """
    Capa adicional de seguridad: redacta posibles PII en el resumen por regex.
    Cumplimiento de políticas de privacidad y seguridad (GDPR/PCI-DSS). Se aplica
    por si el LLM no redacta correctamente; es mejor redactar de más que exponer
    datos sensibles (p. ej. una tarjeta de crédito).
    """
    if not summary or not summary.strip():
        return summary
    text = summary
    # Tarjetas: 13-19 dígitos con espacios o guiones opcionales (formato 4-4-4-4 o 16 seguidos)
    text = re.sub(
        r'\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b',
        '[CARD_REDACTED]',
        text,
    )
    text = re.sub(
        r'\b\d{13,19}\b',
        '[CARD_REDACTED]',
        text,
    )
    # DNI español: 8 dígitos + letra (opcional espacio/guion)
    text = re.sub(
        r'\b\d{8}\s*[A-Za-z]\b',
        '[ID_REDACTED]',
        text,
    )
    # SSN US: XXX-XX-XXXX
    text = re.sub(
        r'\b\d{3}-\d{2}-\d{4}\b',
        '[ID_REDACTED]',
        text,
    )
    return text


# --- 1. Configuración y Logging ---
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s'
)
logger = logging.getLogger('worker')

# Configuración General
STT_ENGINE = os.getenv('STT_ENGINE', 'local').lower()
SUMMARIZE_ENGINE = os.getenv('SUMMARIZE_ENGINE', 'gemini').lower()
SUMMARIZE_FALLBACK_ENGINE = (os.getenv('SUMMARIZE_FALLBACK_ENGINE') or '').strip().lower() or None
SUMMARIZE_ENABLED = os.getenv('SUMMARIZE_ENABLED', 'true').lower() == 'true'
TASK_NAME_STR = os.getenv('TASK_NAME', "tel-callrec-transcriber")
TASK_NAME = TASK_NAME_STR.encode()
GEARMAN_SERVER = os.getenv('GEARMAN_HOST', 'gearman:4730')

# Timeouts para clientes de IA/STT (evitan bloqueo del worker ante proveedores lentos)
AI_TIMEOUT_TOTAL = float(os.getenv('AI_TIMEOUT_TOTAL', '120.0'))
AI_TIMEOUT_CONNECT = float(os.getenv('AI_TIMEOUT_CONNECT', '10.0'))

# Tipos de excepción considerados "timeout" para fallback y logs
_TIMEOUT_EXCEPTIONS: tuple = (requests.exceptions.Timeout, concurrent.futures.TimeoutError, TimeoutError)
try:
    import openai as _openai
    if hasattr(_openai, 'APITimeoutError'):
        _TIMEOUT_EXCEPTIONS = (_openai.APITimeoutError,) + _TIMEOUT_EXCEPTIONS
except ImportError:
    pass

# S3
s3_bucket = os.getenv('S3_BUCKET_NAME')
aws_key = os.getenv('BUCKET_ACCESS_KEY_ID')
aws_secret = os.getenv('BUCKET_SECRET_ACCESS_KEY')

_s3_client: Optional[Any] = None


def get_s3_client() -> Optional[Any]:
    """Cliente S3 singleton (lazy). Reutiliza la misma conexión en cada llamada."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    if not all([s3_bucket, aws_key, aws_secret]):
        logger.error('Credenciales S3 incompletas.')
        return None
    _s3_client = boto3.client(
        's3',
        aws_access_key_id=aws_key,
        aws_secret_access_key=aws_secret,
        endpoint_url=os.getenv('S3_ENDPOINT', None),
        region_name=os.getenv('S3_REGION_NAME', None)
    )
    return _s3_client


def validate_s3_key(s3_key: str) -> str:
    """Valida que sea una clave S3 de archivo .mp3."""
    if not s3_key or not isinstance(s3_key, str):
        raise ValueError("callid (s3_key) vacío o inválido")
    s3_key = s3_key.strip()
    if not s3_key.endswith('.mp3'):
        raise ValueError("La clave S3 debe ser un archivo .mp3")
    if '..' in s3_key:
        raise ValueError("Path no permitido")
    return s3_key


def download_from_s3(s3_key: str, local_path: str) -> bool:
    """Descarga un objeto desde S3 a un path local."""
    s3 = get_s3_client()
    if not s3:
        return False
    try:
        s3.download_file(s3_bucket, s3_key, local_path)
        logger.info(f"Descargado desde S3: {s3_key}")
        return True
    except Exception as e:
        logger.error(f"Error descargando de S3 {s3_key}: {e}")
        return False


def upload_to_s3(local_path: str, s3_key: str) -> bool:
    """Sube un archivo local a S3."""
    s3 = get_s3_client()
    if not s3:
        return False
    try:
        s3.upload_file(local_path, s3_bucket, s3_key)
        return True
    except Exception as e:
        logger.error(f"Error subiendo a S3 {s3_key}: {e}")
        return False


def mp3_to_wav(mp3_path: str, wav_path: str) -> bool:
    """Convierte MP3 a WAV con ffmpeg (16kHz mono para STT)."""
    try:
        cmd = [
            'ffmpeg', '-y', '-i', mp3_path,
            '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
            '-nostats', '-loglevel', 'error', wav_path
        ]
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Error convirtiendo MP3 a WAV: {e}")
        return False


# --- 2. Transcripción ---

class Transcriber:
    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError


class FasterWhisperTranscriber(Transcriber):
    def __init__(self):
        from faster_whisper import WhisperModel
        model_name = os.getenv("WHISPER_MODEL", "small")
        device = os.getenv("FASTER_WHISPER_DEVICE", "cpu")
        compute_type = os.getenv("FASTER_WHISPER_COMPUTE_TYPE", "int8")
        logger.info(f"Cargando faster-whisper {model_name} en {device}...")
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        kwargs = {"vad_filter": True, "beam_size": 5}
        if language:
            kwargs["language"] = language
        segments_iter, _ = self.model.transcribe(audio_path, **kwargs)
        segs = []
        texts = []
        for s in segments_iter:
            t = (s.text or "").strip()
            if t:
                segs.append({"start": s.start, "end": s.end, "text": t})
                texts.append(t)
        return {"text": " ".join(texts), "segments": segs}


class OpenAITranscriber(Transcriber):
    def __init__(self):
        import openai
        self.client = openai.OpenAI(
            api_key=os.getenv('STT_API_KEY'),
            timeout=httpx.Timeout(AI_TIMEOUT_TOTAL, connect=AI_TIMEOUT_CONNECT),
        )
        self.model = os.getenv('OPENAI_WHISPER_MODEL', 'whisper-1')

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        try:
            with open(audio_path, 'rb') as f:
                transcript = self.client.audio.transcriptions.create(
                    model=self.model,
                    file=f,
                    language=language,
                    response_format='verbose_json',
                    timestamp_granularities=['segment']
                )
            text = transcript.text or ""
            segs = []
            if hasattr(transcript, 'segments') and transcript.segments:
                for s in transcript.segments:
                    segs.append({
                        "start": getattr(s, 'start', 0.0),
                        "end": getattr(s, 'end', 0.0),
                        "text": (getattr(s, 'text', None) or "").strip()
                    })
            return {"text": text, "segments": segs}
        except Exception as e:
            if isinstance(e, _TIMEOUT_EXCEPTIONS):
                logger.warning("OpenAI transcription failed due to Timeout: %s", e, exc_info=False)
            else:
                logger.error(f"OpenAI transcription error: {e}")
            raise


def _fetch_available_gemini_models() -> List[str]:
    """
    Consulta la API de Gemini para obtener modelos que soportan generateContent.
    Devuelve IDs sin prefijo 'models/' (ej. gemini-1.5-flash).
    """
    api_key = os.getenv('STT_API_KEY') or os.getenv('GEMINI_API_KEY')
    if not api_key:
        return []
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        models = []
        for m in data.get('models', []):
            name = m.get('name', '')
            if 'generateContent' in m.get('supportedGenerationMethods', []):
                if name.startswith('models/'):
                    models.append(name[7:])  # quitar "models/"
                else:
                    models.append(name)
        return models
    except Exception as e:
        logger.warning(f"No se pudo listar modelos Gemini: {e}")
        return []


# Cache de modelos Gemini disponibles (se rellena al primer 404)
_gemini_available_models: Optional[List[str]] = None


class GeminiTranscriber(Transcriber):
    def __init__(self):
        import google.generativeai as genai
        api_key = os.getenv('STT_API_KEY') or os.getenv('GEMINI_API_KEY')
        genai.configure(api_key=api_key)
        self._genai = genai
        self._primary_model = os.getenv('GEMINI_TRANSCRIPTION_MODEL', 'gemini-1.5-flash')
        self.model = genai.GenerativeModel(self._primary_model)
        logger.info(f"Modelo Gemini transcripción: {self._primary_model}")

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        import google.generativeai as genai
        prompt = "Transcribe este audio exactamente. Devuelve solo el texto hablado."
        if language:
            prompt += f" El idioma es {language}."

        def _do_transcribe(model_id: str) -> Dict[str, Any]:
            model = genai.GenerativeModel(model_id)
            uploaded = genai.upload_file(audio_path, mime_type='audio/wav')
            response = model.generate_content([prompt, uploaded])
            text = (response.text or "").strip()
            return {"text": text, "segments": [{"start": 0, "end": 0, "text": text}]}

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_transcribe, self._primary_model)
                return future.result(timeout=AI_TIMEOUT_TOTAL)
        except Exception as e:
            if "404" not in str(e):
                logger.error(f"Gemini transcription error: {e}")
                return {"text": "", "segments": []}

            # 404: modelo no disponible. Buscar uno que sí esté disponible.
            global _gemini_available_models
            if _gemini_available_models is None:
                _gemini_available_models = _fetch_available_gemini_models()
                if _gemini_available_models:
                    logger.info(
                        "Modelos Gemini disponibles: %s",
                        ", ".join(_gemini_available_models[:5]) + ("..." if len(_gemini_available_models) > 5 else ""),
                    )

            for alt_id in _gemini_available_models:
                if alt_id == self._primary_model:
                    continue
                try:
                    logger.warning(
                        "Modelo %s no disponible (404). Probando %s...",
                        self._primary_model,
                        alt_id,
                    )
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_do_transcribe, alt_id)
                        result = future.result(timeout=AI_TIMEOUT_TOTAL)
                    logger.info("Transcripción exitosa con modelo %s", alt_id)
                    return result
                except Exception as e2:
                    if "404" in str(e2):
                        continue
                    logger.warning("Modelo %s falló: %s", alt_id, e2)

            logger.error(
                "Ningún modelo Gemini disponible. Configura GEMINI_TRANSCRIPTION_MODEL en .env "
                "con un ID de https://generativelanguage.googleapis.com/v1beta/models?key=TU_KEY"
            )
            return {"text": "", "segments": []}


class ElevenLabsTranscriber(Transcriber):
    def __init__(self):
        self.api_key = os.getenv('ELEVENLABS_API_KEY')
        self.base_url = "https://api.elevenlabs.io/v1"

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        if not self.api_key:
            logger.error("ELEVENLABS_API_KEY no configurada")
            return {"text": "", "segments": []}
        url = f"{self.base_url}/speech-to-text"
        headers = {"xi-api-key": self.api_key}
        with open(audio_path, 'rb') as f:
            files = {"file": (os.path.basename(audio_path), f, "audio/wav")}
            data = {}
            if language:
                data["language_code"] = language
            try:
                r = requests.post(url, headers=headers, files=files, data=data or None, timeout=AI_TIMEOUT_TOTAL)
                r.raise_for_status()
                out = r.json()
                text = out.get("text", "")
                return {"text": text, "segments": [{"start": 0, "end": 0, "text": text}]}
            except requests.exceptions.Timeout as e:
                logger.warning("ElevenLabs transcription failed due to Timeout: %s", e, exc_info=False)
                return {"text": "", "segments": []}
            except Exception as e:
                logger.error(f"ElevenLabs transcription error: {e}")
                return {"text": "", "segments": []}


_engine_instance = None


def get_engine(engine_name: str) -> Transcriber:
    global _engine_instance
    if _engine_instance:
        return _engine_instance
    if engine_name == 'local':
        _engine_instance = FasterWhisperTranscriber()
    elif engine_name == 'openai':
        _engine_instance = OpenAITranscriber()
    elif engine_name == 'gemini':
        _engine_instance = GeminiTranscriber()
    elif engine_name == 'elevenlabs':
        _engine_instance = ElevenLabsTranscriber()
    else:
        raise ValueError(f"Motor desconocido: {engine_name}")
    return _engine_instance


# --- 3. Sumarización ---

class Summarizer:
    """Base para proveedores de sumarización por LLM."""

    def summarize(
        self,
        text: str,
        prompt_content: str,
        model_override: Optional[str] = None,
    ) -> str:
        """
        Genera un resumen del texto usando el prompt completo dado.

        :param text: Transcripción completa (usado para comprobar vacío).
        :param prompt_content: Prompt completo a enviar al LLM (instrucciones + separador + transcripción).
        :param model_override: Modelo opcional a usar en lugar del por defecto.
        """
        raise NotImplementedError


class GeminiSummarizer(Summarizer):
    def __init__(self):
        import google.generativeai as genai
        api_key = os.getenv('GEMINI_API_KEY') or os.getenv('STT_API_KEY')
        genai.configure(api_key=api_key)
        self._genai = genai
        self._default_model = os.getenv('SUMMARIZE_MODEL', 'gemini-1.5-flash')

    def summarize(
        self,
        text: str,
        prompt_content: str,
        model_override: Optional[str] = None,
    ) -> str:
        if not text.strip():
            return ""
        model_name = model_override or self._default_model

        def _do_summarize(mid: str) -> str:
            model = self._genai.GenerativeModel(mid)
            response = model.generate_content(prompt_content)
            return (response.text or "").strip()

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_summarize, model_name)
                return future.result(timeout=AI_TIMEOUT_TOTAL)
        except concurrent.futures.TimeoutError as e:
            logger.warning("Gemini summarization failed due to Timeout: %s", e, exc_info=False)
            raise
        except Exception as e:
            if "404" not in str(e):
                logger.error(f"Gemini summarization error: {e}")
                raise

            # 404: probar modelos alternativos
            global _gemini_available_models
            if _gemini_available_models is None:
                _gemini_available_models = _fetch_available_gemini_models()
            for alt_id in _gemini_available_models:
                if alt_id == model_name:
                    continue
                try:
                    logger.warning("Modelo %s no disponible. Probando %s para sumarización...", model_name, alt_id)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_do_summarize, alt_id)
                        return future.result(timeout=AI_TIMEOUT_TOTAL)
                except Exception as e2:
                    if "404" in str(e2):
                        continue
                    logger.warning("Modelo %s falló: %s", alt_id, e2)
            logger.error(f"Gemini summarization error: {e}")
            raise


class OpenAISummarizer(Summarizer):
    def __init__(self):
        import openai
        self.client = openai.OpenAI(
            api_key=os.getenv('STT_API_KEY'),
            timeout=httpx.Timeout(AI_TIMEOUT_TOTAL, connect=AI_TIMEOUT_CONNECT),
        )
        self.model = os.getenv('SUMMARIZE_MODEL', 'gpt-4o-mini')

    def summarize(
        self,
        text: str,
        prompt_content: str,
        model_override: Optional[str] = None,
    ) -> str:
        if not text.strip():
            return ""
        model = model_override or self.model
        try:
            r = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt_content}]
            )
            return (r.choices[0].message.content or "").strip()
        except Exception as e:
            if isinstance(e, _TIMEOUT_EXCEPTIONS):
                logger.warning("OpenAI summarization failed due to Timeout: %s", e, exc_info=False)
            else:
                logger.error(f"OpenAI summarization error: {e}")
            raise


class OpenAICompatibleSummarizer(Summarizer):
    """
    Sumarizador para APIs compatibles con el SDK de OpenAI (DeepSeek, Groq, Ollama, etc.).
    Permite configurar base_url, api_key y model_name desde variables de entorno o inyección.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
    ) -> None:
        import openai
        self.client = openai.OpenAI(
            base_url=base_url or None,
            api_key=api_key,
            timeout=httpx.Timeout(AI_TIMEOUT_TOTAL, connect=AI_TIMEOUT_CONNECT),
        )
        self.model_name = model_name

    def summarize(
        self,
        text: str,
        prompt_content: str,
        model_override: Optional[str] = None,
    ) -> str:
        if not text.strip():
            return ""
        model = model_override or self.model_name
        try:
            r = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt_content}]
            )
            return (r.choices[0].message.content or "").strip()
        except Exception as e:
            if isinstance(e, _TIMEOUT_EXCEPTIONS):
                logger.warning(
                    "OpenAI-compatible summarization failed due to Timeout: %s",
                    e,
                    exc_info=False,
                )
            else:
                logger.error(f"OpenAI-compatible summarization error: {e}")
            raise


_summarizer_instances: Dict[str, Summarizer] = {}

# Prefijos de variables de entorno por proveedor OpenAI-compatible (base_url, api_key, model)
_OPENAI_COMPATIBLE_ENV_PREFIXES: Dict[str, tuple] = {
    'deepseek': ('DEEPSEEK_BASE_URL', 'DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL'),
    'llama': ('LLAMA_BASE_URL', 'LLAMA_API_KEY', 'LLAMA_MODEL'),
}


def _create_openai_compatible_summarizer(prefix_key: str) -> OpenAICompatibleSummarizer:
    """Crea OpenAICompatibleSummarizer leyendo base_url, api_key y model de env con prefijo."""
    base_key, api_key_key, model_key = _OPENAI_COMPATIBLE_ENV_PREFIXES[prefix_key]
    base_url = os.getenv(base_key) or os.getenv('OPENAI_COMPATIBLE_BASE_URL', '')
    api_key = os.getenv(api_key_key) or os.getenv('OPENAI_COMPATIBLE_API_KEY', '')
    model_name = os.getenv(model_key) or os.getenv('OPENAI_COMPATIBLE_MODEL', '')
    return OpenAICompatibleSummarizer(base_url=base_url, api_key=api_key, model_name=model_name)


def get_summarizer(engine_name: str) -> Optional[Summarizer]:
    """Factory: devuelve una instancia de Summarizer por engine (pool lazy, una por engine)."""
    name = (engine_name or '').strip().lower()
    if not name:
        name = SUMMARIZE_ENGINE
    if name in _summarizer_instances:
        return _summarizer_instances[name]
    summarizer: Optional[Summarizer] = None
    if name == 'gemini':
        summarizer = GeminiSummarizer()
    elif name == 'openai':
        summarizer = OpenAISummarizer()
    elif name == 'deepseek':
        summarizer = _create_openai_compatible_summarizer('deepseek')
    elif name == 'llama':
        summarizer = _create_openai_compatible_summarizer('llama')
    else:
        logger.warning(
            "Motor de sumarización desconocido: %s, usando fallback gemini",
            engine_name,
        )
        return get_summarizer('gemini')
    _summarizer_instances[name] = summarizer
    return summarizer


# --- 4. Lógica del Worker ---


def notify_transcription_error(callid: str, msg: str) -> None:
    """Actualiza SpeechAnalysis a ERROR y notifica vía Django Channels."""
    try:
        updater = SpeechAnalysisStatusUpdater()
        updater.update_transcription(callid, status=3, msg=msg)  # ERROR=3
    except Exception as e:
        logger.warning("No se pudo notificar error de transcripción: %s", e)


def process_job(worker, job):
    try:
        try:
            payload_dict = json.loads(job.data.decode('utf-8'))
            payload = TranscriptionJobPayload.model_validate(payload_dict)
        except json.JSONDecodeError as e:
            return json.dumps({"status": "error", "message": f"Payload JSON inválido: {e}"}).encode()
        except PydanticValidationError as e:
            callid = payload_dict.get("callid") if isinstance(payload_dict, dict) else None
            if callid:
                notify_transcription_error(callid, "Invalid payload")
            return json.dumps({"status": "error", "message": f"Payload inválido: {e}"}).encode()

        callid = payload.callid
        try:
            s3_key = validate_s3_key(callid)
        except ValueError as e:
            notify_transcription_error(callid, "Invalid S3 key")
            return json.dumps({"status": "error", "message": str(e)}).encode()

        logger.info(f"Procesando: {s3_key}")

        engine_name = payload.engine or STT_ENGINE
        language = payload.language
        do_summarize = payload.summarize if payload.summarize is not None else SUMMARIZE_ENABLED

        # 1. Descargar MP3 desde S3
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_mp3:
            tmp_mp3_path = tmp_mp3.name
        try:
            if not download_from_s3(s3_key, tmp_mp3_path):
                notify_transcription_error(callid, "Download from S3 failed")
                return json.dumps({"status": "error", "message": "Download from S3 failed"}).encode()

            # 2. Audio para transcribir: faster-whisper acepta MP3; el resto usa WAV
            use_mp3_direct = engine_name == 'local'
            if use_mp3_direct:
                audio_path = tmp_mp3_path
            else:
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_wav:
                    tmp_wav_path = tmp_wav.name
                try:
                    if not mp3_to_wav(tmp_mp3_path, tmp_wav_path):
                        try:
                            os.unlink(tmp_wav_path)
                        except OSError:
                            pass
                        notify_transcription_error(callid, "MP3 to WAV conversion failed")
                        return json.dumps({"status": "error", "message": "MP3 to WAV conversion failed"}).encode()
                    audio_path = tmp_wav_path
                except Exception:
                    try:
                        os.unlink(tmp_wav_path)
                    except OSError:
                        pass
                    raise

            try:
                # 3. Transcribir
                try:
                    transcriber = get_engine(engine_name)
                except Exception as e:
                    notify_transcription_error(callid, "Unavailable Engine")
                    return json.dumps({"status": "error", "message": f"Engine error: {e}"}).encode()

                t_res = transcriber.transcribe(audio_path, language=language)
                try:
                    validated = TranscriptionResult.model_validate(t_res)
                except PydanticValidationError as e:
                    logger.error(f"Transcripción con estructura inválida: {e}")
                    notify_transcription_error(callid, "Invalid transcription structure")
                    return json.dumps({
                        "status": "error",
                        "message": "Invalid transcription structure"
                    }).encode()
                full_text = validated.text
                segments = [s.model_dump() for s in validated.segments]

                # 4. Sumarizar (con fallback y validación Pydantic)
                summary = ""
                if do_summarize and full_text:
                    instructions_text = _resolve_prompt_instructions(payload)
                    prompt_content = f"{instructions_text}\n\n---\n\n{full_text}"
                    summarizer_engine_name = (
                        (payload.summarizer_engine or '').strip().lower()
                        or SUMMARIZE_ENGINE
                    )
                    summarizer = get_summarizer(summarizer_engine_name)
                    model_override = payload.summarizer_model or None
                    try:
                        if summarizer:
                            summary = summarizer.summarize(
                                full_text,
                                prompt_content=prompt_content,
                                model_override=model_override,
                            )
                    except _TIMEOUT_EXCEPTIONS as e:
                        logger.warning(
                            "Sumarización fallida por Timeout con %s: %s",
                            summarizer_engine_name,
                            e,
                        )
                        if SUMMARIZE_FALLBACK_ENGINE and SUMMARIZE_FALLBACK_ENGINE != summarizer_engine_name:
                            fallback = get_summarizer(SUMMARIZE_FALLBACK_ENGINE)
                            if fallback:
                                try:
                                    summary = fallback.summarize(
                                        full_text,
                                        prompt_content=prompt_content,
                                        model_override=model_override,
                                    )
                                    logger.info(
                                        "Sumarización exitosa con fallback: %s",
                                        SUMMARIZE_FALLBACK_ENGINE,
                                    )
                                except Exception as fb_e:
                                    logger.warning("Fallback de sumarización también falló: %s", fb_e)
                    except Exception as e:
                        logger.warning("Sumarización fallida con %s: %s", summarizer_engine_name, e)
                        if SUMMARIZE_FALLBACK_ENGINE and SUMMARIZE_FALLBACK_ENGINE != summarizer_engine_name:
                            fallback = get_summarizer(SUMMARIZE_FALLBACK_ENGINE)
                            if fallback:
                                try:
                                    summary = fallback.summarize(
                                        full_text,
                                        prompt_content=prompt_content,
                                        model_override=model_override,
                                    )
                                    logger.info(
                                        "Sumarización exitosa con fallback: %s",
                                        SUMMARIZE_FALLBACK_ENGINE,
                                    )
                                except Exception as fb_e:
                                    logger.warning("Fallback de sumarización también falló: %s", fb_e)
                    if summary:
                        summary = redact_summary_pii(summary)
                        try:
                            validated_summary = SummaryResult(content=summary)
                            summary = validated_summary.content
                        except PydanticValidationError as ve:
                            logger.warning("Summary no pasó validación Pydantic: %s", ve)
                            summary = ""

                # 5. Prefijo S3 (mismo que la grabación sin .mp3)
                base_key = s3_key[:-4]  # quitar .mp3

                # 6. Subir resultados
                results = []
                final_json = {
                    "meta": {"s3_key": s3_key, "engine": engine_name},
                    "text": full_text,
                    "segments": segments,
                    "summary": summary
                }
                json_key = f"{base_key}.json"
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp_json:
                    json.dump(final_json, tmp_json, ensure_ascii=False)
                    tmp_json.flush()
                try:
                    if upload_to_s3(tmp_json.name, json_key):
                        results.append({"file": f"{base_key}.json", "s3_key": json_key})
                finally:
                    try:
                        os.unlink(tmp_json.name)
                    except OSError:
                        pass

                # Update SpeechAnalysisStatus and notify via Django Channels
                try:
                    updater = SpeechAnalysisStatusUpdater()
                    updater.update_transcription(callid, status=2, transcription_file=json_key)
                except Exception as e:
                    logger.warning("No se pudo actualizar SpeechAnalysis: %s", e)

                if full_text:
                    txt_key = f"{base_key}.txt"
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp_txt:
                        tmp_txt.write(full_text)
                        tmp_txt.flush()
                    try:
                        if upload_to_s3(tmp_txt.name, txt_key):
                            results.append({"file": f"{base_key}.txt", "s3_key": txt_key})
                    finally:
                        try:
                            os.unlink(tmp_txt.name)
                        except OSError:
                            pass

                if summary:
                    summary_key = f"{base_key}.summary.txt"
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp_sum:
                        tmp_sum.write(summary)
                        tmp_sum.flush()
                    try:
                        if upload_to_s3(tmp_sum.name, summary_key):
                            results.append({"file": f"{base_key}.summary.txt", "s3_key": summary_key})
                    finally:
                        try:
                            os.unlink(tmp_sum.name)
                        except OSError:
                            pass

                return json.dumps({"status": "ok", "transcriptions": results}).encode('utf-8')

            finally:
                if not use_mp3_direct and audio_path and os.path.isfile(audio_path):
                    try:
                        os.unlink(audio_path)
                    except OSError:
                        pass
        finally:
            try:
                os.unlink(tmp_mp3_path)
            except OSError:
                pass

    except Exception as e:
        logger.exception("Error fatal en worker")
        try:
            payload_dict = json.loads(job.data.decode("utf-8"))
            err_callid = payload_dict.get("callid") if isinstance(payload_dict, dict) else None
            if err_callid:
                notify_transcription_error(err_callid, "Transcription processing Error")
        except Exception:
            pass
        return json.dumps({"status": "error", "message": "Internal Error"}).encode()


# --- 5. Main ---
if __name__ == "__main__":
    try:
        get_engine(STT_ENGINE)
    except Exception as e:
        logger.critical(f"No se pudo iniciar el motor {STT_ENGINE}: {e}")
        sys.exit(1)

    if SUMMARIZE_ENABLED:
        try:
            get_summarizer(SUMMARIZE_ENGINE)
        except Exception as e:
            logger.warning(f"Sumarizador no inicializado: {e}")

    gm_worker = gearman.GearmanWorker([GEARMAN_SERVER])
    gm_worker.register_task(TASK_NAME, process_job)
    logger.info(f"Worker escuchando en: {TASK_NAME_STR}")
    gm_worker.work()
