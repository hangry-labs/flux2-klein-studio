<p align="center"><a href="../README.md">English</a> · <a href="../nb/README.md">Norsk bokmål</a> · <a href="../pl/README.md">Polski</a> · <a href="../ja/README.md">日本語</a> · <a href="../zh/README.md">简体中文</a> · <strong>Español</strong></p>

# FLUX.2 Klein Studio · Documentación

Esta introducción en español cubre los flujos habituales. Los esquemas detallados de API, informes de medición, parámetros de adaptadores, reglas de publicación e historial de validación se mantienen en inglés. Los enlaces siguientes indican el idioma.

[Resumen del proyecto](../../README.es.md) · [Docker Hub](../dockerhub.es.md).

## Guía breve de uso

**Generar:** describe sujeto, entorno e iluminación. Empieza con cuatro pasos. Ancho y alto deben estar entre 64 y 1920 y ser múltiplos de 16. El botón de ejemplo solo rellena la descripción. Más pasos no garantizan mejores resultados.

**Referencias:** sube entre una y cuatro imágenes o elige un resultado de Galería. Cada archivo puede ocupar hasta 16 MiB. Se corrige la orientación, se compone la transparencia sobre blanco y se limita cada referencia a aproximadamente un megapíxel, alineando sus dimensiones a 16. El orden importa. El modelo base admite varias referencias; los adaptadores de edición, una.

**Relleno verde:** pinta con #00FF00 o amplía el lienzo para añadir bordes verdes. La instrucción fija siempre se incluye; el contenido es opcional. Las marcas pequeñas pueden quedar. **Zoom rojo:** el rectángulo conserva la proporción de salida. Arrastra dentro para moverlo y selecciona fuera para reemplazarlo. Usa el resultado para otro primer plano.

**Galería:** activa el guardado antes de generar. Una edición guardada conserva todas las referencias preparadas y los parámetros, pero no el archivo original sin procesar ni el historial del pincel. Reabrir los parámetros fija la semilla. PNG y WebP conservan los píxeles; JPEG tiene pérdida. Confirmar la eliminación borra todos los archivos locales del trabajo.

**Sistema:** el tiempo de inactividad predeterminado es de 300 segundos. Cero libera el modelo tras generar. Liberar VRAM sin usar conserva los pesos; Liberar ahora también los elimina de memoria. La selección de tamaños afecta a nuevas solicitudes HTTP/MCP y al anuncio de herramientas, no al lienzo local. Los gráficos GPU solo muestrean mientras Sistema está visible.

## Archivos y memoria

El volumen `flux2_klein_studio_data`, montado en `/app/persistent`, conserva modelos, cachés, ajustes y resultados guardados. Reutilízalo al reemplazar el contenedor. Sin guardar en Galería, el resultado es temporal: se elimina al reiniciar o al salir del conjunto de 100 vistas previas. Los resultados guardados solo se eliminan cuando tú los borras. Eliminar el volumen puede borrar tanto la caché como los resultados guardados.

Todos los componentes del modelo permanecen en GPU, sin descarga a CPU o disco. Los pesos se conservan cinco minutos tras el último trabajo de forma predeterminada. La memoria de trabajo sin usar se libera automáticamente; Sistema también permite liberarla al instante. La tabla de tamaños muestra referencias de generación solo con texto, no una garantía de que todas las ediciones quepan en memoria.

## Aplicaciones y agentes

La API de imágenes ofrece `/v1/images/generations` y `/v1/images/edits`, con el modelo `flux2-klein-4b`. MCP usa Streamable HTTP en `/mcp/`; los agentes deben leer primero la herramienta `skill`. Los nombres de tamaños como `square_big` y los identificadores de adaptador no cambian. La API admite `n=1..16`; las solicitudes esperan en una cola FIFO compartida y la GPU genera una imagen a la vez. El ejemplo siguiente usa comillas de Bash.

```bash
curl http://localhost:7860/v1/images/generations -H "Content-Type: application/json" -d '{"model":"flux2-klein-4b","prompt":"A glass robot tending orchids in a sunlit greenhouse","size":"square_big","seed":12345}'
```

## Documentación técnica en inglés

- [Studio en el navegador (inglés)](../studio.md)
- [HTTP y MCP (inglés)](../agent-api.md)
- [Adaptadores e instrucciones fijas (inglés)](../lora.md)
- [Metodología y resultados (inglés)](../benchmarks.md)
- [Capacidades y límites del modelo (inglés)](../model-capabilities.md)
- [Historial de pruebas y validación (inglés)](../validation.md)
- [Publicación de contenedores (inglés)](../RELEASING.md)
- [VRAM con varias referencias (inglés)](../../benchmarks/multiref/README.md)

## Privacidad y condiciones

La generación se realiza localmente. Los resultados y prompts solo se guardan de forma persistente al elegir Galería; la descarga inicial necesita red. Mantén el puerto en localhost o usa un proxy con autenticación adecuada. `STUDIO_API_KEY` protege `/v1` y `/mcp`, no la API local del navegador. Usa referencias con los permisos necesarios y no presentes imágenes generadas como registros auténticos.

El código de la aplicación tiene licencia Apache-2.0. Modelos, adaptadores y dependencias conservan sus propias condiciones. Consulta LICENSE, NOTICE y THIRD_PARTY.md; la licencia de la aplicación no sustituye las condiciones de los componentes.

[LICENSE](../../LICENSE) · [NOTICE](../../NOTICE) · [THIRD_PARTY.md](../../THIRD_PARTY.md).
