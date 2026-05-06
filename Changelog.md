# Changelog - Postcall Actions (oml-773-dev-oml-3)

## Resumen Ejecutivo
La rama `oml-773-dev-oml-3` moderniza la arquitectura de los componentes `callrec_compressor` y `callrec_transcriber` al eliminar su dependencia de volúmenes compartidos en disco con el servidor ACD. El flujo de procesamiento se ha transformado a un modelo *stateful*, donde los workers descargan y suben audios directamente desde S3, actualizan el ciclo de vida de la grabación directamente en la base de datos y notifican a los usuarios en tiempo real mediante WebSockets. Esto aporta mayor escalabilidad, autonomía de los workers y trazabilidad a la plataforma.

## Nuevas Funcionalidades (Features)
- **Desacoplamiento de Almacenamiento (Cloud-Native S3):** Los workers ya no requieren leer desde un directorio local compartido (`ASTERISK_MONITOR_PATH`). En su lugar, descargan los archivos WAV temporales directamente desde S3 utilizando la key correspondiente, los comprimen o transcriben, y resuben los artefactos generados. Finalmente, el compressor se encarga de eliminar el archivo WAV original de S3.
- **Gestión *Stateful* del Ciclo de Vida (`status_updates.py`):** El procesamiento de transcripción ahora interactúa directamente con el estado del sistema. El `callrec_transcriber` actualiza de manera segura el estado de las transcripciones y del análisis de sentimientos en la base de datos (PostgreSQL, tabla `reportes_app_speechanalysis`) usando un UPSERT para evitar registros duplicados tras normalizar el `callid`.
- **Notificaciones en Tiempo Real (Django Channels):** Se incorporó el módulo `DjangoChannelsNotifyer`, que utiliza Redis para enviar notificaciones push a los canales de WebSocket (Django Channels). Esto permite que el supervisor reciba alertas instantáneas en la UI cuando el procesamiento de la llamada haya finalizado.
- **Evolución a Variables Genéricas de S3:** Se refactorizaron las variables de configuración del almacenamiento, pasando de la nomenclatura propietaria `AWS_*` a nombres agnósticos `BUCKET_*` (ej. `BUCKET_NAME`, `BUCKET_ACCESS_KEY_ID`), permitiendo mejor integración con proveedores compatibles con S3.
- **Múltiples Artefactos y Resumen de Llamada:** El transcriber ahora es capaz de extraer mayor valor del audio, generando y almacenando en S3 tres artefactos separados por cada llamada: el archivo JSON con los segmentos y timestamps, el texto plano completo (`.txt`) y un resumen autogenerado de la conversación (`.summary.txt`).

## Impacto y Consideraciones para Despliegue

### Para DevOps:
- **Nuevas Variables de Entorno (Action Required):** 
  - Se debe modificar el inventario de Ansible/Vault para renombrar cualquier variable `AWS_*` a `BUCKET_*`.
  - Como el componente ahora es *stateful*, los pods/contenedores de compressor y transcriber requieren acceso a la base de datos y a Redis. Asegurarse de inyectar: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `POSTGRES_OML_PASSWORD` y `REDIS_HOSTNAME`, `REDIS_PORT`.
- **Eliminación de Bind Mounts:** En la configuración de Docker/Podman, ya no es necesario montar el volumen de grabaciones de Asterisk en los contenedores de `postcall_actions`.
- **Dependencias de Red:** Verificar que los workers tengan conectividad directa hacia el endpoint de la base de datos, los nodos de Redis (específicamente db=2 para notificaciones y db=4 para Channels) y el servicio de S3.

### Para QA (Quality Assurance):
- **Ciclo de Vida del Audio en S3:** Ejecutar llamadas de prueba y validar que el ACD suba el WAV a S3. Luego, corroborar que el compressor descargue el WAV, genere y suba el MP3, y **elimine definitivamente el WAV de S3** sin dejar basura.
- **Pruebas de Base de Datos y Notificaciones:** 
  - Al finalizar una transcripción exitosa, consultar la tabla `reportes_app_speechanalysis` y verificar que exista o se haya actualizado la fila de esa llamada con el `transcription_status = 2` y la ruta correcta del archivo JSON.
  - Desde el panel de supervisión, confirmar que se reciba la notificación WebSocket de fin de transcripción en tiempo real sin necesidad de refrescar la página.
- **Generación de Artefactos de Speech Analytics:** Revisar directamente en el bucket S3 o en la UI que, por cada grabación procesada, se encuentren los tres archivos generados correctamente: el archivo de texto base (`.txt`), el archivo con timestamps (`.json`), y el resumen de la llamada (`.summary.txt`).
