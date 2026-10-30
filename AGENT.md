# AGENT.md — Livre Cloud

Contexto vivo para trabajar en Livre Cloud con Hermes. Este archivo describe el estado operativo actual; puede contener referencias históricas en la sección correspondiente, pero no reemplaza la verificación del código cuando una tarea dependa de un contrato puntual.

## Regla de inicio

- Al comenzar cada sesión nueva, leer una sola vez este archivo como único contexto operativo válido para todos los repositorios de Livre.
- No crear ni mantener `AGENT.md` en los repositorios frontend; si falta contexto local, usar este archivo y no inventar un estado que no esté documentado.
- Todos los servicios y frontends de Livre se despliegan únicamente en Railway. Vercel ya no forma parte del stack y no debe usarse como destino ni referencia de disponibilidad salvo pedido explícito del usuario.
- No leer `CONTEXT.md` ni `CONTEXT.MD`.
- No volver a leer los archivos de contexto durante la misma sesión salvo pedido explícito o modificación explícita.
- Después de leerlos, comprobar el estado real de cada repo (`git status`, rama y remoto) antes de editar o publicar.
- Comparar el contexto con el código real, la base PostgreSQL y los registros de Railway cuando la tarea lo requiera.
- No repetir el contenido completo de estos archivos en cada llamada.

## Repositorios

- `livre-cloud`: backend FastAPI, PostgreSQL, API propia y núcleo operativo.
- `livre-chatbot`: CRM/panel web; consume la API de Livre Cloud.
- `livre-pasajeros`: interfaz del pasajero y seguimiento mediante token privado.
- `livre-conductor`: interfaz del conductor, viajes asignados y GPS autenticado.

Ubicación habitual: `/home/calambre/Documentos/Clientes/Livre/`.

## Estado vigente y pendientes (2026-10-30)

- Los cuatro servicios y PostgreSQL operan en Railway; la fuente activa es Livre Cloud. MAGIIS permanece legado inactivo y no debe probarse ni reactivarse.
- El CRM muestra operaciones, mapa e historial desde endpoints propios. Los horarios visibles del dashboard usan `America/Argentina/Buenos_Aires` explícitamente.
- Crear viaje y programar viaje están implementados. Los viajes programados se guardan como `scheduled` con `scheduled_at` y pasan a `searching` al llegar la hora.
- La migración `20261029_scheduled_trips.sql` está aplicada y registrada en producción. Como `AUTO_MIGRATE=false`, cada migración nueva requiere ejecución controlada antes de usar el código que la necesita.
- El botón `Actualizar` de Gestión de viajes está conectado a `loadCrmData()` y `/crm/travels/live` responde correctamente.
- Pendiente real: probar un flujo completo autorizado de creación y programación con una operación controlada; durante las últimas verificaciones no se creó ningún viaje real.
- Pendientes separados: validación física de GPS/notificaciones en Android, definición de la segunda cuenta administrativa y configuración del Access Token de producción de Mercado Pago para cobros de Livre.

## Orden operativo: WEB HTTPS antes que APK

- El orden obligatorio es verificar y corregir primero el flujo web por HTTPS en producción, y recién después evaluar la APK.
- La validación inicial debe hacerse en un navegador normal, sin APK ni WebView, comprobando polling, respuestas HTTP y actualización del DOM en primer plano, sin F5, minimización ni reapertura.
- Solo cuando el flujo web funcione automáticamente se investiga la APK y el GPS nativo.
- No generar viajes de prueba ni APK por cuenta propia. Toda APK nueva requiere una limitación técnica real que no pueda resolverse remotamente y autorización explícita.

## Links de producción

- **Livre Conductor — web principal:** https://livre-conductor-production.up.railway.app/ — login y panel del conductor, viajes asignados, estados y ubicación.
- **Livre Chat:** https://livre-chatbot-production.up.railway.app/ — chat/IA y CRM para pedidos y conversaciones.
- **Livre Pasajeros:** https://livre-pasajeros-production.up.railway.app/ — solicitud y seguimiento de viajes.
- **Livre Cloud:** https://livre-cloud-production.up.railway.app/ — backend/API central; no es una pantalla web.

Aliases históricos o alternativos: `https://livre-chatbot.vercel.app/`, `https://livre-pasajeros.vercel.app/` y `https://livre-system.github.io/`. No son la operación actual: Vercel queda descartado y no debe investigarse ni usarse como referencia de disponibilidad mientras Railway sea el entorno activo.

## Accesos operativos disponibles

- GitHub: `git` y `gh` para revisar repositorios, commits, ramas, issues, PRs y publicar cambios autorizados.
- Railway: CLI `railway` para revisar servicios, deploys, logs y ejecutar verificaciones read-only. No exponer secretos ni cambiar producción sin autorización explícita.
- Vercel: no forma parte de la operación actual. No revisar ni publicar allí salvo pedido explícito del usuario; un error o `503` de un alias histórico no representa una falla de Livre mientras las aplicaciones Railway estén operativas.

Livre Cloud es la frontera del sistema. Las apps propias y el CRM deben comunicarse únicamente con Livre Cloud, no con proveedores externos ni con bases ajenas.

## Estado operativo actual

MAGIIS ya no se usa en la operación diaria y no debe volver a ser tratado como dependencia activa.

MAGIIS se utilizó históricamente para:

- recolectar/importar datos iniciales;
- obtener geolocalizaciones y referencias de direcciones;
- aportar estructura y datos históricos de clientes, conductores, vehículos, viajes, estados y tarifas;
- alimentar registros o snapshots que hoy se conservan en la base y en el historial de Railway.

La fuente activa actual es Livre Cloud:

- PostgreSQL conserva el modelo y los datos operativos propios;
- Railway aloja el backend y sus registros de ejecución;
- Livre Cloud administra pedidos, asignaciones, estados, ubicaciones, seguimiento y datos del CRM;
- las apps de pasajeros y conductores no llaman a MAGIIS;
- el CRM no llama a MAGIIS.

No iniciar login, scraping, requests, sincronizaciones ni pruebas contra MAGIIS. No usar sus credenciales, endpoints, IDs ni tokens. No reactivar variables históricas de MAGIIS.

Los archivos, migraciones, HARs, tests, columnas y documentos con `magiis` pueden seguir existiendo como histórico o compatibilidad de datos. Su existencia no demuestra que el flujo esté activo. No borrarlos ni migrarlos automáticamente: primero identificar dependencias y hacer una tarea aislada si se decide limpiar el legado.

## Auditoría de referencias MAGIIS en los cuatro repositorios

Auditoría de código/configuración/logs realizada el 2026-10-19, sin escrituras en producción, sin login, scraping, requests ni pruebas contra MAGIIS.

Resultado ejecutivo: no hay una llamada MAGIIS activa en ninguno de los cuatro repositorios ni en los cuatro servicios Railway revisados. `livre-chatbot`, `livre-conductor` y `livre-pasajeros` no tienen referencias operativas MAGIIS en el código buscado ni variables MAGIIS configuradas en Railway. `livre-cloud` conserva módulos de compatibilidad, scripts de pruebas, migraciones, snapshots/documentación y columnas de origen, pero el runtime actual los deja inactivos o los usa solamente como identificación histórica.

Clasificación comprobada en `livre-cloud`:

- **Legado inactivo:** `app/integrations/magiis.py`, `magiis_web.py`, `magiis_crm.py`, `magiis_quotes.py` y `app/memory/magiis.py`. No contienen cliente HTTP ni URLs activas. Sus funciones fallan explícitamente con `RuntimeError` o devuelven estado `disabled`.
- **Legado inactivo:** `app/crm/sync.py` y `scripts/sync_magiis_crm_once.py`. El script todavía puede ser invocado manualmente, pero `sync_all()` sólo devuelve `disabled`, no abre sesión externa y no modifica snapshots. El endpoint `POST /crm/sync` también responde `410` y no sincroniza.
- **Histórico:** `app/crm/historical_migration.py`, `scripts/historical_crm_backfill.py`, `scripts/historical_crm_dry_run.py`, migraciones `004`, `005`, `006`, `20260911_magiis_crm.sql` y `20260913_magiis_vehicle_owners.sql`, además de tablas/campos `magiis_*`, `source_magiis_id`, `magiis_driver_id` y `vehicle_magiis_id`. Conservan identidad, payloads o planes de migración; no forman parte del request operativo actual. El backfill de producción no fue ejecutado.
- **Legado inactivo:** `magiis_test.py` y `magiis_web_test.py`. Aunque documentan login, `trip/create` o `simulateAll`, importan funciones que hoy están deshabilitadas; no son parte del arranque ni de los routers incluidos en `app/main.py`. No ejecutarlos.
- **Histórico:** `docs/magiis-har-endpoints.md`, `1apps.magiis.com.har`, fixtures de tests y `reports/magiis_quotes_audit_2026-09.md`. Son evidencia/contrato histórico, no conexiones.
- **Histórico:** `app/integrations/historical_quote.py` conserva nombres, IDs de servicio y perfiles derivados de cotizaciones históricas; la cotización actual lee `livre_quote_profiles` y calcula localmente. No llama a MAGIIS.
- **Histórico:** `app/crm/router.py` conserva `magiis` en el mensaje de `POST /crm/sync` y en nombres de tablas históricas, pero Operaciones, conductores, pasajeros y viajes consultan PostgreSQL propio (`livre_*`, `livi_trips`) y no clientes externos.
- **Activo, pero no MAGIIS:** mapas y cotización usan Google Maps, Nominatim/OpenStreetMap y OSRM como fuentes externas autorizadas; el fallback no pasa por MAGIIS. Los viajes operativos entran por `/mobility/*`, LIVI/WhatsApp y CRM, y se persisten en `livi_trips`.

Configuración y ejecución:

- En Railway producción, las variables históricas `MAGIIS_CRM_SYNC_ENABLED=0`, `MAGIIS_WEB_CREATE_ENABLED=0` y `MAGIIS_WEB_FLOW_ENABLED=0` están explícitamente desactivadas. También existen nombres/credenciales históricas en `livre-cloud`; sus valores no se documentan ni se exponen. La presencia de una variable no prueba una conexión.
- En Railway, las variables de `livre-chatbot`, `livre-conductor` y `livre-pasajeros` fueron consultadas sólo por nombre: no aparecen variables `MAGIIS_*`. En `livre-cloud` sí quedan nombres y credenciales históricas, con los tres flags de ejecución en `0`; sus valores secretos no se documentan ni se exponen.
- El estado read-only de Railway muestra el entorno `production` con cinco instancias del proyecto (Postgres y los cuatro repos), sin `cronSchedule`; cuatro instancias figuran `RUNNING` y una no tiene instancia activa. Esto confirma que no hay una tarea programada MAGIIS, pero no se usa como prueba de salud funcional de cada aplicación.
- No se encontraron schedulers, workers, webhooks ni tareas de arranque que invoquen `sync_all`, `magiis_web`, `MagiisCrmReader` o un cliente MAGIIS. `app/main.py` incluye routers propios y no importa módulos MAGIIS.
- En los logs de producción de `livre-cloud` revisados de los últimos 7 días no aparecieron coincidencias de login, sync, endpoint, host ni error MAGIIS. La salida filtrada no permite reconstruir toda la actividad, así que esto sólo prueba ausencia de evidencia MAGIIS en ese recorte.
- No se modificaron variables, despliegues, bases, tablas ni procesos de producción.

Riesgos residuales:

- **Medio:** mantener credenciales/nombres MAGIIS en Railway deja una superficie de confusión y podría permitir una reactivación accidental si alguien cambia flags o restaura código antiguo. No se tocaron porque la auditoría no autorizaba cambios.
- **Bajo:** scripts, endpoints 410, migraciones y módulos de compatibilidad pueden inducir a un operador a creer que existe sincronización. La documentación y los tests de deshabilitación lo hacen explícito, pero el legado sigue presente.
- **Bajo:** las columnas `magiis_*` son identificadores históricos; no deben interpretarse como una dependencia de tiempo real ni reemplazarse automáticamente por IDs locales.

Conclusión operativa: CRM/Operaciones, mapas, conductores, pasajeros y viajes no consultan MAGIIS directa ni indirectamente en el código desplegado revisado. Lo que queda es histórico o legado inactivo, con configuración histórica desactivada. La limpieza de variables, scripts o migraciones requiere una tarea separada y autorización explícita.

## Arquitectura activa

```text
Pasajero / WhatsApp / LIVI
        ↓
Livre Cloud (Railway + PostgreSQL)
        ↓
modelo propio: livi_trips, asignaciones, estados, GPS y eventos
        ├── livre-chatbot       (CRM/panel)
        ├── livre-pasajeros    (pedido y seguimiento)
        └── livre-conductor    (viajes asignados y ubicación)
```

ElevenLabs/LIVI sigue siendo un canal de entrada de pedidos, no una integración directa con un proveedor de viajes. Sus llamadas llegan a Livre Cloud, que persiste y procesa el viaje según el contrato vigente.

## Cotización activa

`POST /elevenlabs/quote` cotiza sin crear viajes reales. Resuelve las direcciones con Google Geocoding y usa Nominatim/OpenStreetMap como fallback cuando Google devuelve `ZERO_RESULTS`; luego obtiene distancia vial con Google Distance Matrix y, si Google rechaza la ruta, con OSRM; calcula opciones con perfiles históricos propios guardados en PostgreSQL. No llamar a MAGIIS para cotizar ni inventar precio, coordenadas o distancia si alguna fuente falla.

## Flujo propio de movilidad

Pedido y asignación actuales:

```text
livre-pasajeros
  → POST /mobility/requests
  → livi_trips + tracking_token + assigned_driver_user_id NULL (pedido abierto)
  → livre-conductor /mobility/driver/trips (todos los elegibles)
  → estado y GPS autenticados
  → livre-pasajeros /mobility/tracking/{tracking_token}
```

El pedido de prueba valida Bahía Blanca y alrededores y queda abierto para todos los conductores en servicio (`active = TRUE`) o con GPS actualizado en los últimos 2 minutos. La aceptación usa una actualización condicional sobre `assigned_driver_user_id IS NULL`: el primer conductor que acepta queda asignado y cierra la disponibilidad para los demás. La ubicación del conductor se guarda en `livi_trips`; nunca debe venir del navegador del pasajero.

Contrato actual de movilidad:

```text
POST /mobility/requests
GET  /mobility/tracking/{tracking_token}
GET  /mobility/trips/{trip_id}
POST /mobility/trips/{trip_id}/status
POST /mobility/trips/{trip_id}/location
GET  /mobility/driver/trips
POST /mobility/driver/trips/{trip_id}/status
POST /mobility/driver/trips/{trip_id}/location
```

Los endpoints de conductor deben comprobar que el usuario autenticado está habilitado y que el viaje está asignado a ese conductor. Un JWT válido por sí solo no alcanza.

El pasajero recibe solamente datos mínimos mediante su token privado. No mezclar el token de seguimiento con un JWT ni con una identidad de panel.

## Identidades: mantenerlas separadas

1. `livre_users.id`: usuario autenticado del panel o cuenta interna de Livre.
2. `livre_driver_accounts.user_id`: vínculo de una cuenta interna habilitada para operar como conductor.
3. `system__caller_id` / `whatsapp_user`: pasajero que originó un pedido por WhatsApp.
4. `conversation_id`: conversación concreta de LIVI.
5. `livi_trips.id`: viaje operativo propio.
6. `tracking_token`: acceso privado de seguimiento del pasajero.

No inferir una identidad a partir de otra. No crear usuarios de panel para pasajeros de WhatsApp. No usar `whatsapp_user` como `livre_users.id`. No filtrar el CRM por el usuario que inició sesión cuando la consulta representa la operación general.

## LIVI y viajes

El flujo esperado es:

```text
origen escrito
→ confirmación
→ destino escrito
→ confirmación
→ cotización local válida
→ precio
→ confirmación explícita
→ registro del viaje
→ confirmación
→ cierre de conversación
```

La cancelación debe mantenerse separada del flujo de alta. Si el pasajero expresa intención de cancelar, no pedir origen/destino ni cotizar: usar únicamente el contrato propio vigente para cancelar, verificar el resultado y comunicarlo sin inventar.

No afirmar que un viaje fue creado, cancelado, cotizado o asignado si la respuesta real no lo confirma. No inventar precios, conductores, vehículos, coordenadas, estados ni IDs.

Las direcciones deben conservar el texto confirmado por el usuario. Las geolocalizaciones y rutas activas deben salir de una fuente disponible y verificable; si un dato no está en PostgreSQL o en un registro confiable, reportar la ausencia en vez de completarlo a mano.

## Datos y persistencia

- PostgreSQL/Railway es la fuente de verdad de la operación actual.
- Persistir viajes, estados, asignaciones, conductor, vehículo, ubicación, eventos y timestamps en el modelo propio.
- La última coordenada del conductor se conserva como histórico, pero los mapas sólo muestran una posición con heartbeat de los últimos 2 minutos (`sessionActive=true`).
- La actividad en línea y la vigencia de la coordenada son criterios distintos: una sesión puede vencer antes y una coordenada vieja puede mostrarse como última ubicación conocida, nunca como GPS activo.
- La memoria y los embeddings sirven para contexto o texto libre; no son la fuente de verdad de viajes ni estados.
- No borrar ni recrear tablas, viajes, embeddings, memorias o históricos sin una decisión explícita y un plan reversible.
- No reparar datos históricos inventando relaciones entre identidades.
- Toda lectura o diagnóstico de producción debe ser read-only salvo autorización expresa.
- Sanitizar logs, respuestas y reportes: nunca exponer secretos, tokens, credenciales o datos sensibles.

## Reglas de trabajo

- Investigar primero el repo, archivo, contrato y estado real que correspondan.
- Antes de editar, informar qué archivos se van a tocar cuando el cambio sea sensible.
- Hacer cambios mínimos, aislados y reversibles. No reemplazar archivos completos ni refactorizar subsistemas sin necesidad.
- No mezclar cambios entre backend, CRM, pasajero y conductor sin una razón concreta.
- Preservar los flujos de cotizar, registrar y cancelar.
- No asumir que README, documentación histórica, variables de Railway o nombres de columnas describen el flujo activo: corroborar con código, base y logs.
- Si una tarea encuentra legado MAGIIS, no llamar al proveedor para validarlo. Documentar la referencia y trabajar sobre datos/contratos propios.
- Validar cada cambio con tests, checks sintácticos, contratos HTTP o una verificación read-only apropiada.
- Informar exactamente qué se encontró, qué se cambió, qué se validó y qué quedó pendiente.
- Actualizar este archivo solo cuando haya cambios importantes y confirmados en arquitectura, contratos, operación o reglas del proyecto; no por cada tarea menor.
- Hacer commit y push siempre después de cambios de proyecto, salvo que el usuario indique explícitamente lo contrario.

## Archivos y zonas sensibles

No modificar automáticamente integraciones históricas, migraciones o rutas de voz solo porque contengan referencias a MAGIIS. Antes de retirarlas o adaptar su contrato, revisar usos, datos existentes y despliegue. En particular, tratar como sensibles:

- `app/integrations/magiis_web.py`;
- `app/integrations/magiis.py`;
- `app/integrations/magiis_crm.py`;
- `app/voice/elevenlabs_trip.py`;
- migraciones y columnas históricas de `livi_trips`;
- configuración y variables de Railway.

Esto no significa que MAGIIS siga activo: significa que el legado debe limpiarse de forma controlada y separada.

## Reporte final esperado

Responder breve y en criollo con:

- qué se encontró;
- qué se cambió;
- validaciones ejecutadas y resultado real;
- pendientes o riesgos;
- repositorios y archivos afectados;
- si hubo commit/push.

## Historial de decisiones y verificaciones

## Implementación de comunicaciones y conducción por voz (2026-09-29)

- La app de pasajeros incorpora mensajería del viaje desde el seguimiento, con actualización periódica y canal separado del handie.
- La app de conductor conserva la operación manual y agrega conducción por voz opcional, activada por botón y operada con la palabra clave “Livi”. El botón permite desactivarla para privacidad; la escucha no se inicia al abrir la app.
- Los comandos de voz se limitan a acciones laborales: aceptar, iniciar, finalizar viaje y abrir Base/Handie. La mensajería escrita continúa disponible en paralelo.
- El conductor dispone de un canal Base/Handie separado del chat pasajero. El CRM muestra ese canal y permite transmitir mensajes a un handie abierto.
- Backend: `app/mobility/router.py`, `app/mobility/schemas.py` y migración `app/migrations/20260929_driver_voice_channels.sql`. Los canales usan tablas propias de Livre y no mezclan credenciales ni contratos de MAGIIS.
- La mensajería web utiliza polling corto e interfaz de notificación dentro del viaje; la integración nativa FCM/APNs queda como etapa posterior de publicación móvil.
- Validar en staging con un viaje real de prueba, dos identidades separadas y la migración aplicada antes de producción. No usar datos reales ni probar IDs arbitrarios.

## Verificación técnica 2026-09-29

- `PYTHONPATH=. uv run --with-requirements requirements.txt --with pytest pytest -q`: 9 tests passed.
- `python -m compileall -q app`: OK.
- `npm ci && npm run verify` en pasajeros: build web y parseo inline OK.
- Parseo `node --check` de scripts inline de conductor y CRM: OK.
- PDF `docs/livre-arquitectura-producto.pdf`: 3 páginas A4, sin encriptación.
- La prueba local de `app.main` no pudo resolver el hostname privado de Railway; la validación equivalente se completó contra producción usando `railway run` y `DATABASE_PUBLIC_URL`, en modo solo lectura.
- Railway verificado: proyecto `fantastic-nurturing`, entorno `production`, servicios `livre-cloud`, `livre-conductor`, `livre-pasajeros` y Postgres online.
- Producción respondió `GET /health` con `200 {"status":"ok"}` y publicó las rutas de movilidad/chat en OpenAPI.
- PostgreSQL de Railway respondió en modo lectura: base accesible, 30 tablas públicas, y existen `livre_handie_sessions` y `livre_trip_messages`.
- No se ejecutaron migraciones, inserts, viajes, mensajes ni operaciones externas.
- `npm ci` informó 3 vulnerabilidades moderadas de dependencias existentes; no se aplicó `npm audit fix` automático para evitar cambios no revisados.
- Los repositorios quedaron sin modificaciones rastreadas pendientes y con sus commits publicados en `origin/main`; permanecen únicamente archivos de evidencia no rastreados que ya existían.

## Correcciones conductor y despliegue 2026-09-29

- Se corrigió la doble implementación del listener de voz en `livre-conductor/index.html`; “Aceptar por voz” ahora activa el mismo modo controlado por “Livi” y el cierre de sesión no llama funciones inexistentes.
- Se añadieron manejo de errores de micrófono y estado visible para evitar escucha silenciosa o botones que aparenten funcionar sin permiso.
- Se agregaron los dominios de producción Railway de conductor y pasajeros a CORS en `livre-cloud/app/main.py`, evitando bloqueos del navegador para viajes, Base y mensajería.
- Verificado en producción: conductor desplegado contiene el arreglo, `/health` devuelve 200, CORS devuelve el origen Railway del conductor y `/voice-eleven/config` responde con agente `livre`, idioma español y formatos PCM 16 kHz.
- No se generaron viajes, mensajes, inserts ni cambios en PostgreSQL durante estas pruebas.

## Estado actual del front de conductor después del reinicio 2026-09-29

- `livre-conductor/index.html` fue reiniciado visualmente por etapas para eliminar la ensalada de UI: primero se dejó en blanco y luego se restauró únicamente el login.
- Estado publicado actual de `livre-conductor`: solo se muestra la pantalla de autenticación; el panel conductor, mapa, viajes, GPS, mensajería y ventanas flotantes permanecen ocultos.
- La lógica JavaScript existente se conservó debajo del login para resolver las funciones por partes, sin borrar todavía los contratos de viajes, estados, ubicación, voz ni mensajería.
- El login visible contiene usuario, contraseña, ingreso y registro. Se verificó localmente que el formulario carga, que el login aparece y que `#appView` permanece oculto.
- El último commit publicado en `livre-conductor` es `28f21da` (`feat: restore conductor login screen`). El commit anterior dejó el front completamente blanco: `fa15189`.
- El problema de viajes nuevos no se considera resuelto: hay que verificar si el pedido llega como `assigned` para el conductor autenticado antes de restaurar el panel operativo.
- No modificar el front completo hasta validar primero login, autenticación y transición controlada al panel.

## Estado confirmado de GPS y visor CRM (2026-09-30)

- `livre-conductor` solicita permiso de ubicación al entrar al panel mediante la API de geolocalización del navegador y reutiliza `startGps()`/`watchPosition()` para mantener el GPS.
- `livre-cloud` guarda la ubicación autenticada en `livre_driver_accounts.current_latitude`, `current_longitude` y `location_updated_at` mediante `POST /mobility/driver/location`; la ubicación de viaje también se conserva en `livi_trips` mediante el endpoint de viaje existente.
- `GET /crm/operational` incorpora las posiciones actuales de las cuentas activas de conductor Livre en `lastPlace.latitude/longitude`, identificadas por `livre_users.id`, sin mezclar conductores.
- `livre-chatbot` consume `/crm/operational` para el visor. El mapa Leaflet se inicializa una sola vez por contenedor; los refreshes actualizan markers sin `map.remove()`, sin recrear `L.map()`, sin reemplazar el contenedor y sin modificar la cámara.
- El visor no debe usar MAGIIS como fuente operativa de ubicaciones. Las posiciones activas provienen de Livre Cloud.
- El visor del mapa no hace polling automático. `showMapViewer()` carga una vez al abrir y el botón `Actualizar` llama manualmente a `loadMapViewer()`. El polling general del CRM no debe ejecutarse con `crmMode = 'map'`.
- Commits publicados de esta conexión: `livre-cloud` `53a20b3` (`fix: expose live driver GPS to CRM map`), `livre-chatbot` `e0841bf` (`fix: preserve CRM map during refresh`), `7ed9b68` (`fix: preserve CRM map camera during polling`) y `2134f1b` (`Isolate map view from CRM polling`).

## Flujo ElevenLabs/LIVI y GPS real confirmado (2026-10-01)

- `POST /elevenlabs/trip` crea el mismo tipo de viaje operativo que `POST /mobility/requests`: un único registro en `livi_trips` con `tracking_token`, `assigned_driver_user_id` inicialmente NULL y `status = assigned`; queda abierto para el despacho colectivo.
- La asignación de ElevenLabs ya no selecciona un único conductor al crear el viaje. Todos los conductores en servicio o con GPS actualizado en los últimos 2 minutos lo reciben en `GET /mobility/driver/trips`; el primer `accept` gana mediante actualización atómica y los demás dejan de verlo como disponible.
- `livre-conductor` acepta como única fuente de ubicación `navigator.geolocation`; no usar IP, coordenadas fijas ni fallback demo. Ante error o permiso denegado debe enviar cero coordenadas, mostrar el error y permitir reintentar.
- La ubicación válida se envía a `POST /mobility/driver/location` y, cuando corresponde, a `POST /mobility/driver/trips/{trip_id}/location`.
- Cambios publicados: `livre-cloud` `f03e850` (`fix: assign ElevenLabs trips to drivers`) y `livre-conductor` `0e79846` (`fix: require real browser GPS`). Se verificaron `compileall`, `node --check`, `git diff --check` y ausencia de marcadores de fallback demo. No se generaron viajes ni coordenadas falsas durante la validación.

## Notificación de tracking por WhatsApp confirmada (2026-10-01)

- En los viajes reales `74` y `75` se confirmó la cadena `assigned → arriving → send_tracking_message()`; ElevenLabs recibió la solicitud pero respondió `HTTP 422`.
- La causa fue el contrato de `POST /v1/convai/whatsapp/outbound-message`: `template_params[0]` requería `type = "body"` además de `parameters`.
- `app/voice/elevenlabs_trip.py` envía ahora `template_params: [{"type": "body", "parameters": [{"text": tracking_url}]}]`; no se modificaron el prompt de LIVI, `registrar_viaje`, conductor, tracking, destinatario `whatsapp_user`, transición de estados ni `end_call`.
- La respuesta exitosa guarda `tracking_message_sent_at` y `tracking_message_conversation_id`; los errores quedan en `tracking_message_error`.
- Commit publicado: `55c3b69` (`fix: send WhatsApp template body params`). Railway production desplegó ese commit en `98ec35fb-1bc8-434d-b640-76d72e5f253f`, estado `SUCCESS`; `GET /health` respondió `200 {"status":"ok"}`.
- Verificaciones: `python -m py_compile app/voice/elevenlabs_trip.py`, `git diff --check` y `PYTHONPATH=. uv run --with-requirements requirements.txt --with-requirements requirements-dev.txt pytest -q` (`9 passed`).
- No se ejecutó un envío artificial. La entrega real de WhatsApp queda pendiente de comprobar con el próximo viaje: `assigned → arriving → outbound 200 → tracking_message_sent_at != NULL → tracking_message_conversation_id` y recepción de `tracking_link`.

## Despacho colectivo y APK del conductor (2026-10-02)

- `livre-cloud` publica los viajes de WhatsApp/LIVI sin conductor preasignado; el pedido queda visible para cada cuenta en servicio o con GPS actualizado en los últimos 2 minutos.
- La primera aceptación se resuelve atómicamente con `assigned_driver_user_id IS NULL`; los demás conductores dejan de recibirlo como disponible.
- Cambios publicados en `livre-cloud`: commit `b3a9036` (`Broadcast WhatsApp trips to available drivers`), Railway production en `SUCCESS`, `GET /health` respondió `200 {"status":"ok"}`.
- Tests del backend: `PYTHONPATH=. uv run --with pytest --with httpx --with psycopg2-binary pytest -q` → `9 passed`; `python -m compileall -q app` → OK.
- APK debug actual del conductor: `/home/calambre/Documentos/Clientes/Livre/livre-conductor/dist/app-debug.apk`.
- Copia equivalente con ícono rojo: `/home/calambre/Documentos/Clientes/Livre/livre-conductor/dist/red-icon/livre-conductor-debug-apk/app-debug.apk`. Ambos tienen el mismo SHA-256: `b7296cafba2c2e1701be6a2bf1f749a0968a65d2696e5319359972f2373f7c25`.
- El APK es `debug`, no una versión release firmada para Play Store.

## Estado confirmado de hardening nativo de livre-conductor (2026-10-03)

- `livre-conductor` mantiene una sesión nativa separada mediante `SessionStore`; solo persiste token y API cuando la API tiene esquema `https`, host válido y no contiene userinfo ni fragmento. Una sesión inválida se limpia y el servicio no debe operar con ella.
- El frontend valida también el parámetro `?api=` y vuelve al API HTTPS por defecto cuando el valor no es válido. No se modificaron contratos del backend ni se agregaron endpoints.
- `LocationService` usa un único scheduler de un hilo para serializar el polling nativo y las tareas de red. El polling background mantiene intervalo de 5 segundos y el `GET /mobility/driver/trips` conserva timeout de conexión y lectura de 4500 ms. El polling foreground JS de 5 segundos/4500 ms quedó preservado.
- El servicio incorpora `ConnectivityManager.NetworkCallback`: suspende consultas sin red validada, dispara una consulta al recuperar conectividad y aplica backoff para timeout, DNS/TLS y respuestas 5xx. 401 y 403 limpian la sesión nativa y detienen el servicio.
- El polling y la actualización de ubicación verifican la sesión persistida antes de operar. Con sesión inválida el servicio se detiene y devuelve `START_NOT_STICKY`; con sesión válida devuelve `START_STICKY`.
- Las notificaciones de viajes usan el canal nativo `livre_trips` de importancia alta y `trip_horn.wav`. `TripNotificationStore` reserva cada `trip_id` de forma persistente y atómica, migra el registro anterior, conserva hasta 7 días/500 entradas y evita duplicados entre consultas, concurrencia y reinicios.
- Se detectan notificaciones globalmente deshabilitadas o canal de viajes deshabilitado; la Activity puede informar al conductor y abrir los ajustes correspondientes.
- Al tocar una notificación, `pendingTripId` se conserva hasta que el WebView carga y JavaScript confirma exactamente ese `trip_id` mediante `confirmTripOpened`. Si el viaje ya no aparece, está terminal o fue tomado por otro conductor, se limpia únicamente el pendiente sin modificar el backend.
- La Activity y el servicio tienen una máquina de permisos basada en `PermissionGate` para ubicación precisa, ubicación en segundo plano, notificaciones y GPS del dispositivo. La Activity revisa el estado en `onResume`; no inicia el servicio operativo sin los requisitos evaluados, informa permisos/GPS deshabilitados y detiene el servicio ante revocación. El GPS nativo evita duplicar el GPS JavaScript dentro del shell Android.
- El servicio notifica la invalidación de sesión a la Activity; logout limpia la sesión y detiene el servicio. La corrección de compilación aplicada en `MainActivity.java` agregó el import faltante de `IntentFilter` y eliminó el import duplicado de `Intent`.

Validaciones realizadas sobre este bloque:

- `node --check js/app.js`: OK.
- `git diff --check`: OK, incluido después de corregir el import de `IntentFilter`.
- Parseo XML de `android/app/src/main/AndroidManifest.xml`: OK.
- Checks estáticos propios para dedupe, canal HIGH, sonido, detección de notificaciones, pending trip y preservación del polling foreground: OK.
- Búsqueda de patrones de credenciales en los archivos nuevos/modificados: 0 coincidencias.
- `tools/verify_release.py` quedó desactualizado: falla porque todavía exige el literal `notified_trip_ids` dentro de `LocationService.java`, mientras el almacenamiento fue extraído a `TripNotificationStore.java`.

Pendientes explícitos:

- No se pudo compilar Android en el entorno auditado porque no estaban disponibles `gradle`, `gradlew`, `java` ni `javac`.
- Quedan pendientes las pruebas en dispositivo/emulador de permisos, revocación, reinicio del proceso, canal de notificaciones, navegación desde WebView frío, pérdida de conectividad y carrera de polling.
- No se generó un APK nuevo, no se hizo commit ni push durante este trabajo.

## Estado del CRM en Railway (2026-10-16)

- `livre-chatbot` es el CRM/panel web y está publicado en Railway en `https://livre-chatbot-production.up.railway.app/`.
- El commit publicado es `00b7316915ce8e0017237416754e139c835b027c` (`fix(crm): refresh and display travel data`), sincronizado con `origin/main`.
- El cambio incorpora filtro inicial con todos los estados, actualización automática de viajes sin F5 y fallback para mostrar los datos `tracking_*` de conductor y vehículo.
- Railway terminó el deployment `479a2bab-dc9c-4790-86cd-9a120ac770e1` en estado `SUCCESS`; el servicio quedó online.
- La URL pública respondió HTTP 200 y el `js/app.js` publicado contiene los cambios verificados.
- `.env.local` existe localmente como archivo no trackeado y no fue incluido en el commit; nunca publicar ni exponer su contenido.
- No queda pendiente de deploy para este cambio. No reabrir el análisis del CRM salvo que aparezca un problema nuevo o el usuario lo pida.

## Migración histórica del CRM a Railway (2026-10-03)

- `livre-chatbot` quedó preparado para Railway con `package.json` y `server.js` en el commit `ad2175a90d7f22a1573518cedfeb687d18f1a3ee` (`chore: prepare CRM for Railway`), publicado en `origin/main`.
- Se verificó `node --check server.js` y `npm test`: 4 tests pasaron.
- El dominio público definitivo del CRM ya está documentado arriba y el deploy actual está confirmado.
- No modificar `allow_origins` de `livre-cloud` sin verificar primero el contrato CORS real y el dominio activo.

## Diagnóstico GPS nativo en Redmi confirmado (2026-10-14)

- La APK diagnóstica activa es `livre-conductor/dist/app-debug.apk`, paquete `com.livre.conductor.diagnostic`; no se generó ni reinstaló otra APK durante esta verificación.
- En el Redmi, Google Play Services está disponible y Fused acepta solicitudes `PRIORITY_HIGH_ACCURACY`; una solicitud aceptada no garantiza que exista un fix para entregar.
- La señal GNSS puede quedar temporalmente sin fix: el proveedor GPS llegó a informar `satellites=0`, `maxCn0=0`, `meanCn0=0`, y Maps tampoco mostró inicialmente una ubicación actual.
- La causa no es un rechazo de permisos ni un fallo permanente de Fused. El sistema tenía ubicación habilitada (`location_mode=3`) y permisos concedidos; Maps llegó a advertir que la Precisión de la ubicación debía activarse.
- La prueba repetida del 2026-10-14 confirmó recuperación sin modificar código, APK ni producción: Fused activó el fallback GPS, luego entregó callbacks con precisión `23.6 m` y `14.616 m`, y la app obtuvo `POST /mobility/driver/location HTTP 200` en dos ocasiones.
- La pantalla de Livre mostró marcador verde actual y estado `Disponible` después de recuperar señal.
- Comparación importante: la sesión anterior también tuvo callback válido de `7.1 m` y luego `lose location`; el comportamiento es intermitente y depende de que el Redmi consiga un fix GNSS/Fused, no de que la solicitud sea aceptada.
- Para nuevos diagnósticos de GPS, primero revisar Google Play Services, Maps, `dumpsys location` y Logcat; no generar APK ni cambiar configuración/producción hasta separar señal, estado del dispositivo y código.

## Backups locales confirmados (2026-10-15)

- Backup local de repositorios generado en `/home/calambre/Backups/Livre/20261015_014714/`.
- Incluye 8 copias locales de `livre-cloud`, `livre-chatbot`, `livre-conductor` y `livre-pasajeros` bajo `Documentos/Clientes/Livre` y `Documentos/Documentos/ll/Livre`.
- Cada repositorio está guardado completo en un archivo `tar.zst`, incluyendo `.git`, cambios sin commit y archivos no rastreados. Los 8 archivos pasan `zstd -t` y checksum mediante `repos/SHA256SUMS`.
- Ese backup también contiene `database.sql`, pero su checksum guardado no coincide con el archivo actual. No tratar esa copia de base como íntegra ni usarla para restaurar sin regenerarla o validarla.

## Backup de Railway confirmado (2026-10-15)

- Backup del proyecto `fantastic-nurturing`, entorno `production`, generado en `/home/calambre/Backups/Livre/railway/20261015_032300/`.
- Incluye un dump PostgreSQL real en formato custom: `database.dump`, checksum `database.dump.sha256` e inventario `database.restore-list.txt`.
- `pg_restore --list` leyó correctamente 234 entradas y el checksum del dump pasó. No se hizo restore completo en una instancia temporal.
- Incluye metadata de servicios, deployments y volumen, más las variables de los 5 servicios (`livre-cloud`, `livre-conductor`, `livre-chatbot`, `livre-pasajeros` y `Postgres`). Los archivos con variables contienen secretos y tienen permisos privados `600`; nunca exponer sus valores.
- El volumen observado es `postgres-volume`, montado en `/var/lib/postgresql/data`, con 5 GB y estado `Ready`.
- Ambos backups fueron de solo lectura: no ejecutaron migraciones, DDL, DML, deploys, push ni cambios de infraestructura. Los backups permiten reconstruir el sistema, pero no restauran automáticamente proyecto Railway, cuenta, dominios ni servicios externos.

## Cierre operativo y regla comercial confirmados (2026-10-15)

- Estado de cierre de la sesión: `livre-cloud` (backend) está desplegado y operativo en Railway; `livre-chatbot` (frontend/CRM) está desplegado y operativo en Railway. Las aplicaciones que se deben usar mañana son las URLs Railway de la sección de producción, no los aliases de Vercel.
- No queda pendiente operativo para esta noche. El error/`503` de Vercel se descarta porque Vercel ya no se usa. No reabrir esa investigación salvo pedido explícito.
- Regla comercial definitiva: el cliente paga el 100% del precio total del viaje; Livre retiene una sola vez el 15% de ese total; el conductor recibe el 85% restante. El 15% no es un descuento adicional aplicado después al conductor.
- Fórmula canónica: `libre_amount = total_cliente * 0.15`; `driver_amount = total_cliente * 0.85`; `libre_amount + driver_amount = total_cliente`. Para `$5.054,00`: Livre `$758,10`, conductor `$4.295,90`, cliente `$5.054,00`.
- La regla es idéntica para viajes originados desde la aplicación y desde WhatsApp/LIVI. El precio confirmado al cliente, `price`/`originalFare`, conserva el total oficial; `finalFare` representa el 85% del conductor cuando ese campo corresponde al contrato operativo. Nunca volver a descontar el 15% sobre `finalFare`.
- El flujo de confirmación de WhatsApp fue corregido para enviar la tarifa oficial completa del cliente, no el importe neto del conductor. La misma separación debe conservarse en API, CRM, historial, cierres y cualquier vista o exportación.
- Cambios comerciales intermedios que separaban o mostraban una tarifa comercial adicional fueron revertidos antes del cierre: backend `3f905fc` revierte `c20c66e`; frontend `1571c7a` revierte `4be5844`. Los commits finales publicados son backend `d77cdc6` (`Fix commercial fare split without double discount`) y frontend `d6dccf7` (`Send official fare when confirming trips`). Ambos repositorios quedaron sincronizados con `origin/main`.
- La estructura de producción se mantuvo sin nuevas migraciones ni columnas comerciales. No se creó otra base ni otro proyecto. La base existente y la tarifa oficial son la fuente de verdad.
- La cotización de producción fue probada en modo sin creación de viaje: una respuesta real dio tarifa cliente `$4.028,00`, Livre `$604,20` y conductor `$3.423,80`; la identidad aritmética fue verificada. El ejemplo exacto de `$5.054,00` fue verificado en tests.
- La prueba de producción no creó viajes, pagos ni liquidaciones; quedó como verificación técnica de cotización. No afirmar que hubo una operación real por esa prueba.
- Validaciones cerradas: suite completa de `livre-cloud` con `35 passed`; tests focalizados de tarifa/estado con `13 passed`; tests del chatbot aprobados; `node --check`/parseos JavaScript y `git diff --check` aprobados; `GET /health` de backend respondió HTTP 200; deployments Railway terminaron en estado `SUCCESS`.
- Las pruebas informadas se consideran aprobadas para el cierre de hoy. Mañana, si hace falta, revisar únicamente el funcionamiento desde las aplicaciones desplegadas en Railway, sin tocar backend, base, migraciones, tarifas ni infraestructura por iniciativa propia.
- Regla de trabajo para mañana: primero observar y documentar; no generar viajes, pagos, cambios de base ni reintentos contra MAGIIS. Si aparece un problema, separar si es UI, contrato HTTP, autenticación, datos o despliegue antes de editar.

## Verificación pasiva de la web Railway y pendientes operativos (2026-10-15)

- Las aplicaciones públicas de conductor, CRM/chatbot y pasajeros respondieron HTTP 200 en Railway. `livre-cloud` respondió `GET /health` con HTTP 200 y `{"status":"ok"}`; `GET /openapi.json` respondió HTTP 200.
- Los endpoints protegidos `GET /mobility/driver/trips` y `GET /auth/me` respondieron HTTP 401 (`Token requerido`) sin credenciales. No se probó con credenciales reales ni se alteraron usuarios o contraseñas.
- El frontend desplegado del conductor conserva polling de viajes cada 5 segundos (`TRIPS_POLL_MS = 5000`), consulta `GET /mobility/driver/trips`, usa `cache: 'no-store'`, controla solicitudes simultáneas y maneja expiración de sesión. La actualización sin F5 queda pendiente de comprobar con una sesión autenticada y un viaje real ya existente.
- El historial del conductor consume `GET /mobility/driver/history` y `GET /mobility/driver/shift-closures`; la interfaz contempla fecha, origen, destino, estado, tarifa total, importe del conductor y comisiones. Los importes no entregados por el backend no deben inventarse. Falta validar esos valores con datos reales autenticados.
- La regla comercial respaldada por código y tests sigue siendo: cliente paga 100%, Livre retiene 15% una sola vez y conductor recibe 85%. La cotización de producción se verificó sin crear viaje; no afirmar que esa prueba fue una operación real.
- El CRM publicado todavía contiene textos de interfaz que dicen `MAGIIS` en estados de historial, viajes, vehículos, choferes y liquidaciones, aunque las llamadas observadas apuntan a Livre Cloud. No usar MAGIIS como fuente operativa; corregir esa nomenclatura antes de considerar el CRM listo para uso diario sin reservas.
- No quedó comprobado de punta a punta el recorrido WhatsApp → Livre Cloud → web del conductor ni la confirmación de WhatsApp; hacerlo requiere una operación autorizada o un viaje real ya existente. No crear viajes, pagos, liquidaciones ni usuarios solo para probarlo.
- La cuenta y permisos concretos de Sergio no quedaron validados con login real. Confirmarlos antes de presentarle la prueba diaria, sin cambiar contraseña ni crear otro usuario.
- Validaciones locales de esta revisión: `python -m compileall -q app` OK; `node --check` del frontend conductor OK. La suite Python no arrancó en el entorno por falta de `pg_config`; el repositorio del conductor no tiene `package.json`, por lo que `npm test` no aplica.
- Prioridad siguiente, sin implementar todavía: eliminar referencias visibles a MAGIIS del CRM; luego ejecutar una prueba autorizada del flujo real y verificar actualización sin F5, historial y reparto económico en las interfaces.

## Estado unificado de pasajeros/conductores y APK Android (2026-10-17)

- La aplicación Android unificada es `livre-pasajeros-main`; el paquete único es `com.livre.pasajeros`. El módulo conductor está empaquetado localmente en `assets/public/driver/`; no se abre la URL remota del conductor ni se instala una segunda APK.
- El login único ofrece los modos `Pasajero` y `Conductor` antes de autenticar. Pasajero usa `POST /auth/login`; conductor usa `POST /auth/driver/login` con `device_id`. Una cuenta de conductor no puede autenticarse por el flujo pasajero: `POST /auth/login` devuelve `403 Esta cuenta debe ingresar como conductor` cuando la cuenta tiene un registro activo en `livre_driver_accounts`.
- `livre-cloud` corrigió el login de conductor para consultar roles dentro de la sesión de base abierta; el uso de un cursor cerrado causaba `500 Internal Server Error` durante el login válido. Commit publicado: `52c1133` (`fix: keep driver role query inside database session`). Railway `/health` respondió HTTP 200 después del push.
- Los roles JWT actuales son exclusivos para el flujo: `["passenger"]` para pasajeros y `["driver"]` para conductores. Las operaciones de conductor están protegidas con `require_driver`; no confiar en ocultar botones del frontend como autorización.
- No se ejecutaron migraciones, DDL, seeds ni cambios manuales en PostgreSQL para estos cambios.
- La APK integra `MainActivity`, `LocationService`, `PermissionGate`, `SessionStore` y `TripNotificationStore` en el mismo paquete. El host Android expone `AndroidGps` para el conductor y `AndroidLocation` para el pasajero.
- La Activity ya no usa `AppTheme.NoActionBarLaunch` como tema persistente. Usa `AppTheme.NoActionBar`; `splash.png` no debe quedar visible detrás del WebView. El header dejó de usar `logo_livre.png`, que era una composición grande que se deformaba al reducirse; los headers usan un logo `L` generado por CSS.
- El GPS del pasajero usa primero `AndroidLocation.requestCurrentLocation()`, que consulta última ubicación GPS/red y luego una solicitud puntual nativa. `navigator.geolocation` queda como fallback. Si el GPS del sistema está apagado, la app abre ajustes, conserva el intento y reanuda al volver mediante `onNativeLocationResume`.
- El bottom sheet del pasajero puede arrastrarse por la manija, tocarse para alternar abierto/cerrado y colapsarse hasta dejar la manija visible. La vista ejecuta `invalidateSize()` de Leaflet después del login y al mostrar tracking.
- Las sugerencias de origen/destino usan Nominatim/OpenStreetMap con `countrycodes=ar`, `viewbox=-64,-33,-56,-42`, `bounded=1`, idioma español y `User-Agent` propio. El resultado se filtra a Bahía Blanca/Bahía Blanca, Buenos Aires/Provincia de Buenos Aires y se muestra en un dropdown visual Livre; no usar el `datalist` nativo como UI final.
- El panel conductor muestra tarifa del pasajero (`fare`/`final_fare`) y tarifa del conductor (`driver_fare`) separadas. No calcular importes comerciales en JavaScript.
- El conductor puede aceptar un viaje mediante `POST /mobility/driver/trips/{trip_id}/status` con `arriving`. El rechazo actual es local al conductor/dispositivo mediante `livre_driver_rejected_trips`; no cancela el viaje del pasajero ni impide que otro conductor lo acepte. Persistencia server-side de rechazos requiere una estructura de datos nueva y no está implementada para respetar la regla de no tocar la base.
- Los controles `available`, `unavailable` y `out_of_service` consumen `GET/PUT /mobility/driver/operational-status`, muestran el estado confirmado por backend y recargan viajes al cambiar.
- El chat conductor-pasajero usa `GET/POST /mobility/driver/trips/{trip_id}/messages` y se refresca cada 5 segundos mientras el viaje está activo. Los mensajes se escapan antes de renderizarse. La notificación actual de mensajes es visual/sonora mientras la app está abierta; push con la app cerrada requiere FCM/APNs, tokens de dispositivo y almacenamiento backend aún no implementados.
- APK debug unificada verificada: `C:\proyecto_livre\projects\livre-pasajeros-main\android\app\build\outputs\apk\debug\app-debug.apk`. La build debe comprobarse con `node --check js/auth.js`, `node --check driver/js/app.js`, `npm run verify`, `npm run cap:sync`, `./gradlew :app:clean :app:assembleDebug --no-daemon` desde `android/` y `apksigner verify --verbose`.
- Las verificaciones Android locales confirman `BUILD SUCCESSFUL` y firma APK v2. ADB no debe considerarse prueba física cuando `adb devices -l` esté vacío; todavía hay que validar en dispositivo el primer arranque, selección de rol, login conductor, GPS con ubicación apagada/encendida, mapa, sheet, chat y notificaciones.
- No afirmar que las notificaciones de mensajes funcionan con la app completamente cerrada hasta integrar y probar FCM/APNs. No afirmar que el rechazo está persistido globalmente hasta implementar almacenamiento server-side.

## Migración histórica e identidades verificadas (2026-10-19)

- La migración histórica verificada conserva las identidades separadas por sistema. Las fuentes locales válidas son `livre_driver_accounts.magiis_driver_id -> user_id`, `livre_crm_vehicles.source_magiis_id` y `livre_crm_passengers.source_magiis_id`; no usar MAGIIS como fuente operativa del CRM.
- La vinculación conductor-vehículo quedó verificada con 182 pares en `livre_driver_vehicles`: 182 relaciones esperadas, 0 duplicados, 0 huérfanos y 0 vinculaciones para los cinco conductores sin vehículo. La operación fue idempotente y no modificó relaciones existentes.
- La auditoría read-only del 2026-10-19 encontró 1.516 viajes históricos con `driver_magiis_id`; los 1.516 tienen conductor local resoluble. No hay viajes operativos actuales con `livi_trips.magiis_driver_id` para persistir, por lo que no hay escritura de viajes propuesta.
- La auditoría de pasajeros no encontró `passenger_magiis_id` histórico utilizable ni candidatos de mapeo estable en la tabla de viajes. No vincular viajes-pasajeros por nombre, teléfono, `whatsapp_user` o texto libre; una futura escritura requiere un mapeo de identidad explícito y revisado.
- No modificar viajes, pagos, liquidaciones, APKs ni otras entidades durante una migración de identidades sin autorización explícita. Presentar antes el SQL exacto, las tablas afectadas, conteos esperados y riesgos; ejecutar sólo después de aprobación.
- Regla financiera obligatoria: el cliente paga 100%; Livre retiene 15% una sola vez; el conductor recibe 85%. No duplicar la comisión en viajes, pagos o liquidaciones.
- No asumir que HTTP 200, `health`, `openapi` o `deploy SUCCESS` prueban funcionamiento de negocio. Verificar el flujo real autorizado, sus datos y su efecto persistido; no modificar datos reales para probar.
- La fuente de respaldo comprobada del estado de esta migración es `/home/calambre/Backups/livre/20261019_111631`; el dump PostgreSQL fue restaurado con éxito en un clúster aislado y los checksums están en `SHA256SUMS`.

## Auditoría funcional del CRM histórico (2026-10-19)

- La auditoría de código confirmó que pasajeros y propietarios se consultan desde `livre_crm_passengers` y `livre_crm_vehicle_owners`, con filtros, conteo total y paginación local. Vehículos se consulta desde `livre_crm_vehicles` con filtros y paginación. No se agregó ninguna dependencia operativa de MAGIIS.
- Se corrigió el endpoint local de conductores: antes tomaba sólo un `vehicle_label` con `LIMIT 1`; ahora agrega todos los vínculos locales por conductor en `vehicles`, `vehicle.plate` y `vehicleDescription`. Esto permite mostrar los 182 vínculos conductor-vehículo sin ocultar relaciones adicionales.
- Se corrigió la pantalla de propietarios: antes solicitaba como máximo 100 registros y podía ocultar 21 de los 121 propietarios migrados; ahora usa páginas de 50, muestra total, página actual y controles anterior/siguiente.
- Los conteos históricos esperados siguen siendo 20.469 pasajeros, 121 propietarios, 136 vehículos y 182 vínculos conductor-vehículo. La comprobación sobre el dump/restauración aislada permanece como fuente de conteos; no se hicieron lecturas ni escrituras en producción.
- No se crearon viajes, no se modificaron datos reales, pagos, liquidaciones ni APKs. No se detectaron escrituras de esquema o datos necesarias; no hay plan de migración pendiente por esta auditoría.
- Validaciones: `npm test` en `livre-chatbot` pasó 10/10; `python -m py_compile app/crm/router.py` pasó; `git diff --check` pasó en ambos repositorios. La suite Python histórica no pudo ejecutarse en este entorno porque `pytest` no está instalado.
- Pendiente: ejecutar una prueba visual/read-only autenticada contra un entorno CRM autorizado para confirmar el render final y tiempos de respuesta; no usar producción sin permisos.

## Auditoría de Operaciones: origen, vigencia y actualización (2026-10-19)

- La pantalla Operaciones del CRM consume `GET /crm/operational` desde `livre-chatbot/js/app.js`; no consulta MAGIIS ni un endpoint de sincronización heredado.
- El endpoint arma la respuesta en cada request desde PostgreSQL local mediante `_operational_snapshot()` y no usa Redis, caché persistente, tabla de resumen ni snapshot diario.
- Tabla principal de estados: `livre_driver_accounts`, unida con `livre_users`. Sólo considera `active = TRUE`; el estado visible `available`, `unavailable` u `out_of_service` sale directamente de `operational_status`.
- Tabla de ubicación: `livre_driver_accounts.current_latitude`, `current_longitude` y `location_updated_at`. `lastSee` es el ISO de `location_updated_at`; no significa que el conductor esté actualmente disponible por sí solo.
- Tabla de viajes: `livi_trips`. Para cada conductor se toma el viaje no terminal más reciente (`status NOT IN ('completed','cancelled')`, ordenado por `updated_at DESC`, `id DESC`). El estado de viaje y `tracking_*` salen de esa fila local.
- Tabla de vehículos: `livre_driver_vehicles`, vinculada con `livre_crm_vehicles` mediante `vehicle_magiis_id = source_magiis_id`; la consulta es local y no llama al servicio MAGIIS.
- La pantalla web debe calcular filtros, KPIs visibles y mapa sobre la misma colección `drivers` recibida; no debe mezclar totales globales con una lista parcial. El estado operativo no apaga el GPS: `unavailable` y `out_of_service` sólo afectan la disponibilidad de nuevos viajes. Para mostrar `Disponible` se exige además `sessionActive=true`; una cuenta con estado por defecto `available` pero sin sesión GPS queda en `No disponible` efectivo y sigue visible en `Todos`. `lastPlace` depende de coordenadas y vigencia de `location_updated_at`.
- La respuesta expone actividad separada por origen: `activity.whatsapp.trip_requests` y `activity.crm.trip_requests`; los viajes realizados y cancelados se derivan de `activity.trips.by_status_and_type`.
- Los indicadores de actividad de Operaciones se calculan con consultas locales limitadas al día calendario argentino vigente (`00:00` a `00:00` siguiente): viajes realizados, cancelados y pedidos por origen. Los contadores de disponibilidad, choferes visibles y GPS son estado actual y no se ponen en cero a medianoche.
- La tabla visible de conductores no representa una última sincronización: muestra la lectura actual de las columnas anteriores y los vínculos locales de vehículos. Las tablas de pasajeros, propietarios y vehículos históricos no alimentan estos contadores de Operaciones.
- Actualización: abrir la vista y el botón `Actualizar` hacen un GET; el polling del CRM está configurado a 5 segundos (`CRM_POLL_MS = 5000`) y excluye el modo mapa. No hay cron, tarea programada, job de 24 horas ni caché identificado en el código revisado. Por eso la observación previa de actualización diaria no queda respaldada como mecanismo de esta pantalla.
- Evidencia de vigencia: el código demuestra lectura en tiempo real al solicitar el endpoint, pero no hay `refreshed_at`, `snapshot_at` ni auditoría de ingestión en la respuesta. `lastSee` sólo prueba cuándo se escribió la ubicación del conductor, no cuándo se actualizó cada contador.
- Estado de confianza: la fuente técnica es local y válida para lo que exista en `livre_driver_accounts`/`livi_trips`, pero la vigencia operativa de cada indicador no se puede demostrar sólo con la pantalla. `realTrips`, `testTrips`, clientes y kilómetros requieren verificar que los campos de clasificación/datos estén poblados y mantenidos.
- Validación read-only de esquema y consultas preparada contra PostgreSQL de Railway con transacción `SET TRANSACTION READ ONLY`; no se ejecutaron inserts, updates, viajes, sincronizaciones MAGIIS ni migraciones. La ejecución autenticada de la consulta productiva no dejó salida verificable en el entorno CLI actual, por lo que no se inventan conteos ni una última fecha de actualización.

### Matriz de Operaciones

| Indicador/tabla visible | Fuente real y criterio | Última actualización comprobable | Confianza | Acción recomendada |
|---|---|---|---|---|
| Total conductores | `livre_driver_accounts` + `livre_users`, `active=TRUE`, `COUNT(*)` | En cada GET; sin timestamp de snapshot | Media | Exponer `generated_at` y auditar altas/bajas |
| Disponibles | `operational_status='available'` sobre cuentas activas | En cada GET; escritura del estado no registrada en la respuesta | Media | Verificar con lectura autenticada y registrar `status_updated_at` |
| No disponibles | `operational_status='unavailable'` sobre cuentas activas | En cada GET; sin evidencia de última escritura | Media | Igual: timestamp y prueba contra el estado del conductor |
| Fuera de servicio | `operational_status='out_of_service'` sobre cuentas activas | En cada GET; sin evidencia de última escritura | Media | Igual; no derivarlo de MAGIIS |
| En viaje | Conductores con último `livi_trips` no terminal | En cada GET; depende de `livi_trips.updated_at` | Media-baja | Validar estados terminales y registrar timestamp de cálculo |
| Total viajes activos | Filas `livi_trips` con estado no terminal | En cada GET; no es “viajes de hoy” | Media-baja | Renombrar la métrica o mostrar período y definición explícitos |
| Viajes reales/prueba | `livi_trips.activity_type` agrupado | No demostrable sin comprobar población del campo | Baja hasta auditar NULLs | Completar/validar clasificación; no inferir por apariencia |
| Clientes individuales | `livi_trips.passenger_type` | No demostrable sin comprobar población | Baja hasta auditar NULLs | Validar contrato del campo y mostrar “sin datos” si falta |
| Kilómetros | `livi_trips.distance_km` | No demostrable sin comprobar población | Baja hasta auditar NULLs | No mostrar cero como dato operativo; usar null/guion |
| Tabla de conductores | `livre_driver_accounts`, GPS local y `livre_driver_vehicles` | GPS: `location_updated_at`; resto sin timestamp específico | Media | Mostrar `lastSee` y antigüedad calculada; alertar GPS vencido |

- Conclusión: no está confirmado que los números sean decoración estadística ni que sean correctos. Sí está confirmado que la pantalla dejó de depender de MAGIIS y calcula bajo demanda con tablas locales. La vigencia de los estados depende de que los endpoints de Livre sigan escribiendo esas tablas; para los contadores derivados de clasificación, la confianza queda baja hasta verificar datos poblados y una lectura autenticada de producción.
- No cambiar la fuente, reactivar sincronizaciones con MAGIIS ni modificar datos hasta presentar un plan con indicador afectado, campo faltante, migración requerida, backfill, prueba read-only y rollback.

## Cierre de pendientes: vínculos, contadores y GPS (2026-10-19)

- El backfill preparado usa únicamente `livre_driver_vehicles.vehicle_magiis_id = livre_crm_vehicles.source_magiis_id`, exige correspondencia única y sólo asigna `livre_driver_vehicles.vehicle_id`; no actualiza `updated_at`, no toca otras columnas y no fue ejecutado. SQL y rollback están en `Backups/Livre/20261019T120928Z/phase1-link-plan.md`.
- Resultado auditado: 182/182 relaciones resolubles, sin duplicados ni huérfanos. La ausencia de FK en `vehicle_id` queda documentada; el preflight del SQL la compensa con validaciones explícitas.
- El endpoint de Operaciones ahora expone `trips.status_semantics`: sólo estados operativos explícitos cuentan como abiertos; `completed`, `cancelled`, `magiis_error` y `cancel_error` son terminales históricos; cualquier estado no conocido queda separado como `unknown`.
- Los 68 `completed` sin `activity_type` permanecen `completed_unclassified`; no se convierten en `real` ni `test`. No se modificaron filas productivas.
- Test agregado: `test_trip_status_semantics_does_not_promote_errors_to_open`, cubre 68 no clasificados, 17 `magiis_error`, 1 `cancel_error` y un estado futuro desconocido.
- GPS: el código confirma inicio al abrir el panel, `watchPosition`/servicio nativo, POST al endpoint y persistencia local. No hay logs productivos suficientes para ubicar el corte entre dispositivo, permisos, envío, endpoint y persistencia. La comprobación faltante es una sesión autorizada con logs correlacionados de permisos/callback, POST (status sanitizado) y lectura posterior de `location_updated_at`; no se simularán coordenadas.
- La validación autenticada read-only queda preparada en `/tmp/livre_prod_route_readonly.py` y requiere una sesión iniciada por el usuario; no se almacenan credenciales, cookies ni tokens.

## Corrección de vínculo conductor-vehículo en Operaciones (2026-10-20)

- La tarjeta mostraba al conductor y la patente, pero `associated_vehicle_model` y `associated_vehicle_color` quedaban siempre en `NULL`; por eso aparecía `Vehículo: No informado` aunque existiera un vínculo.
- `_operational_snapshot()` ahora relaciona `livre_driver_vehicles.vehicle_magiis_id` con `livre_crm_vehicles.source_magiis_id` y devuelve la patente, el modelo y el color del vehículo local.
- La patente mantiene fallback: `livre_crm_vehicles.plate` → `livre_driver_vehicles.vehicle_label` → `livi_trips.tracking_plate`. Modelo y color hacen fallback a `livi_trips.tracking_vehicle_model` y `livi_trips.tracking_vehicle_color`.
- El runtime no necesita ejecutar el backfill de `livre_driver_vehicles.vehicle_id` para mostrar estos datos; el vínculo se resuelve por las claves históricas estables. No se modificaron filas productivas.
- Regresión agregada en `tests/test_operational_status.py`: verifica que el snapshot consulte `livre_crm_vehicles` y use `model`/`color` del vehículo vinculado.
- Validación: `PYTHONPATH=. .venv/bin/pytest -q` → `69 passed`; `python -m compileall -q app` → OK; `git diff --check` → OK.
- Publicado en `main` con commit `7228f6e` (`Vincular vehiculo local con conductor en operaciones`). Railway production quedó `Online` y `/health` respondió HTTP 200.

## Corrección de historial, cierres y regla comercial (2026-10-19)

- `_history_response()` y `_driver_fare()` aplican una sola vez la regla comercial: el cliente paga el total bruto, Livre retiene 15% y el conductor recibe 85%.
- El cálculo usa `original_fare` y cae a `fare` cuando `original_fare` es NULL. `total_revenue` conserva el bruto y `final_fare` no recibe una segunda deducción.
- Con tarifa bruta `3200.00`, el resultado esperado es conductor `2720.00`, comisión `480.00` y cliente `3200.00`.
- `GET /mobility/driver/history` y `POST /mobility/driver/shift-closures` comparten `_history_response()`. `GET /mobility/driver/shift-closures` sólo lee cierres persistidos.
- `/crm/agency-commissions` es un camino independiente basado en `livre_crm_commissions`; no mezclarlo con la liquidación del conductor sin una decisión comercial explícita.
- Regresiones agregadas en `tests/test_commercial_fare.py` cubren viajes completados y no completados, `original_fare`, fallback a `fare`, 85/15 y preservación de `final_fare`.
- Validación local: `PYTHONPATH=. .venv/bin/pytest -q` → `59 passed`; `python -m compileall -q app` → OK; `git diff --check` → OK.
- No se ejecutaron migraciones, escrituras productivas, despliegues ni operaciones contra MAGIIS. La validación autenticada de datos reales sigue pendiente.

## Regla comercial de tarifa del conductor y snapshots (2026-10-20)

- `service_type_id = "2"` identifica Livre Ahorro: el conductor recibe el 100% del importe bruto y la etiqueta debe indicar `0% comisión`.
- Para otros servicios, el snapshot comercial vigente determina la tarifa: conductor 85% y comisión 15%; no volver a descontar si `final_fare` ya es neto.
- El servicio (`service_type_id`) es autoritativo frente a `discount_percentage` o snapshots antiguos. No usar un porcentaje de snapshot obsoleto para etiquetar o recalcular Ahorro.
- En viajes `completed` sin `final_fare`, conservar el importe histórico disponible en `fare`/`original_fare`; no aplicar un fallback comercial ni recalcular históricos.
- El contrato backend/frontend debe conservar nombres compatibles (`service_type_id`, `driver_fare`, `commission_percentage`); toda corrección debe probar tanto el importe como la etiqueta visible.
- Regresión obligatoria: reproducir un snapshot Ahorro obsoleto (por ejemplo, bruto `5054.00` y neto histórico `4295.90`) y verificar que la API y la tarjeta muestran el bruto y `0% comisión`.
- Validación de producción: comprobar el asset JavaScript publicado y la respuesta autenticada de API cuando haya credenciales legítimas; no inventar viajes, credenciales ni datos. No declarar resuelto el bug sólo porque el bundle coincide con el commit.
- Corrección implementada en `app/mobility/router.py`, `tests/test_commercial_fare.py`, `js/app.js` y `tests/status.test.js`; commits coordinados: `4b4b34f` (`livre-cloud`) y `23b434b` (`livre-conductor`).

## Corrección de persistencia de cierres de turno (2026-10-20)

- Producción devolvía HTTP 500 al guardar y consultar cierres porque la tabla existente `livre_driver_shift_closures` no tenía las columnas financieras que el runtime ya utilizaba: `total_revenue`, `driver_revenue` y `commission`.
- La migración original definía esas columnas para instalaciones nuevas, pero no reparaba tablas creadas con una versión anterior del esquema. Por eso el error real era `psycopg2.errors.UndefinedColumn: column "driver_revenue" ... does not exist`.
- Después de reparar las columnas, el POST llegó a insertar correctamente, pero FastAPI devolvía HTTP 500 al validar la respuesta: la base productiva devuelve un UUID para `id` y `DriverShiftClosure` esperaba un entero.
- Se agregó `app/migrations/20261020_driver_shift_closure_columns.sql`, una migración aditiva e idempotente con `ADD COLUMN IF NOT EXISTS`; no modifica filas existentes ni requiere backfill.
- `DriverShiftClosure.id` y el parámetro `closure_id` del PDF ahora son `str`, compatibles con los UUID persistidos en producción.
- La corrección cubre ambos endpoints que comparten la tabla: `POST /mobility/driver/shift-closures` y `GET /mobility/driver/shift-closures`.
- Regresiones agregadas en `tests/test_commercial_fare.py` para verificar la migración y aceptar el UUID real de producción.
- Validado en producción: el POST autenticado responde correctamente sin HTTP 500; no se creó otro cierre productivo para probarlo.

## PDF y presentación de Historial y cierre (2026-10-20)

- El cierre se persiste en `livre_driver_shift_closures` dentro de Livre Cloud. Actualmente no se replica automáticamente en un módulo del CRM; para mostrarlo allí hay que definir un destino explícito, por ejemplo una entidad de cierres/liquidaciones del conductor.
- El CRM ahora expone `/crm/driver-shift-closures` como consulta autenticada de solo lectura y muestra esos registros en `Historial y cierres`, con filtros por fecha y branding Livre.
- La pantalla toma los importes persistidos (`total_revenue`, `driver_revenue`, `commission`) sin recalcularlos ni crear cierres nuevos.
- Validación local del cambio: `PYTHONPATH=. .venv/bin/pytest -q tests/test_commercial_fare.py` → `21 passed`; `npm test` del CRM → `11 passed`; compile/sintaxis y `git diff --check` OK.
- El PDF ahora incluye recursos PDF válidos (fuente Helvetica, página, contenido, xref y trailer), escapa caracteres especiales y fue cubierto con lectura mediante `pypdf`.
- La vista `Historial y cierre` mantiene el comportamiento actual y usa el acento rojo de Livre en tarjeta, encabezado, filtros y botón.
- Se retiró del CRM la tarjeta/canal visual `Base / Handie` (incluido su CSS y polling de mensajes) de todos los items por ahora; no se eliminaron los endpoints ni la lógica del canal en Livre Cloud.

## Gráficos globales de demanda y recuperación de login (2026-10-20)

- La distribución de viajes del CRM usa los objetos visuales existentes del frontend, pero ahora representa el total de viajes (`livi_trips`) y no sólo los programados.
- El gráfico horario agrupa por hora las últimas 24 horas para identificar cuándo se concentra la demanda.
- El gráfico diario agrupa los últimos 15 días para identificar qué días concentran más pedidos.
- El backend expone la distribución global desde `app/crm/router.py`; el frontend la renderiza desde `livre-chatbot/js/app.js` y su estructura existente en `index.html`.
- El despliegue de `livre-cloud` falló al iniciar porque `app/migrations/20261020_trip_state_flow.sql` intentaba validar estados históricos no incluidos en la lista nueva. El síntoma visible era timeout en login de `livreuser` y `502 Application failed to respond`.
- La migración quedó con `CHECK (...) NOT VALID`: conserva filas históricas con estados antiguos y valida los estados de las filas nuevas sin modificar datos productivos.
- Corrección publicada en `livre-cloud` con commit `4d75bae` (`Allow legacy trip states during migration`). Railway production quedó `Online`.
- Verificación posterior: `GET /health` respondió HTTP 200 con `{"status":"ok"}` y el endpoint de login respondió HTTP 401 ante credenciales inválidas; esto confirma que el servicio y la ruta de autenticación están operativos. No se almacenaron ni probaron credenciales reales.
- Validaciones del cambio: `python -m compileall -q app` y `git diff --check` OK. La suite `pytest` no estaba instalada en el entorno usado para esta corrección.

## Corrección del Histórico de viajes del CRM (2026-10-21)

- `livre-chatbot` consumía `GET /crm/travel-history`, pero la tabla filtraba columnas antiguas `Col1`…`Col15`; el contrato actual de Livre Cloud devuelve campos propios como `id`, `created_at`, `whatsapp_user`, `tracking_driver_name`, `tracking_plate`, `tracking_vehicle_model`, `origen`, `destino`, `status` y `updated_at`.
- El frontend fue alineado con ese contrato, manteniendo el historial como lectura de datos propios de Livre Cloud y sin llamadas a MAGIIS.
- Los importes ausentes del resumen ahora se muestran como `—`, no como `ARS 0,00`, para evitar interpretar un dato faltante como importe cero.
- Se agregó `hideTravelHistory()` al cambiar a LIVI IA, CRM, mapa u otra salida del flujo, evitando que la vista quede superpuesta o bloquee la navegación.
- Regresiones agregadas en `livre-chatbot/tests/crm-local-data.test.js` para validar campos reales del endpoint y la salida correcta de la vista.
- Validación local: `node --check js/app.js`, `node --test tests/*.test.js` → `19 passed, 0 failed`, y `git diff --check` OK.
- Publicado en `livre-chatbot` con commit `edc165d` (`Fix travel history rendering and navigation`) y push a `main`.
- En la comprobación del 2026-10-21 el deploy de `livre-chatbot` todavía estaba en transición y el asset HTTPS podía servir el bundle anterior. Ese estado quedó superado por los despliegues posteriores y no debe usarse como estado actual; validar siempre el asset si se investiga una regresión del CRM.

## Cambios previos del CRM, conversaciones y layout (2026-10-21)

- `livre-chatbot` incorporó el selector de conversaciones privadas de LIVI y la navegación lateral para cambiar entre conversaciones sin perder respuestas transmitidas (`9de74ae`, `a441b25`, `b3b8cd7`, `53f3bbd`, `901daf4`, `c1e69e8`).
- Se corrigió la visibilidad del workspace al cambiar de panel y se eliminó el bloqueo de layout provocado por `#chat-workspace[hidden]` con `display: flex`; la regla vigente debe conservar `display: none !important` (`74f6696`, `987c29e`).
- Se eliminó del HTML y del JavaScript el botón flotante independiente de LIVI (`#livi-floating`) para evitar una segunda entrada de chat y superposiciones en móvil/dark mode (`94cf91e`). Las reglas CSS históricas que aún contengan `.livi-floating` no prueban que el componente siga montado; verificar siempre markup y listeners activos.
- La vista de conversaciones se restauró después de la corrección de layout: el historial debe ser visible al entrar a LIVI IA y no quedar oculto permanentemente (`9e2833c`).
- La lista de conversaciones, burbujas y botones fueron alineados visualmente con la navegación existente, manteniendo soporte para modo claro, modo oscuro y viewport móvil. No declarar el cambio disponible sólo por el commit: comprobar el DOM y el asset publicado.
- El Histórico de viajes fue corregido en `edc165d`: usa los campos reales de `GET /crm/travel-history`, oculta correctamente la vista al salir hacia chat/CRM/mapa y muestra `—` cuando faltan importes. La regresión local dejó 19 tests aprobados.
- Los cambios de layout y conversaciones se validaron localmente con `node --check js/app.js`, `node --test tests/*.test.js` y `git diff --check`; no se crearon viajes ni se alteraron datos productivos.
- Railway muestra el servicio `livre-chatbot` con deploy `SUCCESS`, pero la verificación HTTPS posterior todavía devolvió el bundle anterior (`Col1` y sin `hideTravelHistory`). Esto significa que el deploy activo no contiene aún `edc165d`, aunque GitHub `main` sí está actualizado. No informar que el Histórico está corregido en producción hasta que el asset HTTPS contenga `['id', 'ID viaje']` y `function hideTravelHistory()`.
- `livre-chatbot` conserva cambios locales no versionados en `.env.local` y `.gitignore`; no incluirlos en commits ni exponer valores.

## Estado de Railway y cuenta administrativa (2026-10-22)

- Railway tuvo una suspensión temporal registrada en ese momento; ese estado quedó superado. Actualmente los servicios operativos están online en Railway; no usar esta nota histórica para diagnosticar disponibilidad actual.
- La cuenta administrativa `smoreno` fue creada en producción con roles `admin`, `crm` y `crm_admin`; sus credenciales no se documentan en este archivo.
- La segunda cuenta administrativa queda pendiente de definir y crear con credenciales propias.
- La cuenta `smoreno` fue verificada con login real y acceso a las rutas protegidas del CRM; no se modificaron viajes ni datos operativos.

## Corrección de viajes aceptados no visibles en el CRM (2026-10-27)

- El CRM no mostraba en Gestión de viajes/“En curso” los viajes aceptados por un conductor porque filtraba únicamente `confirmed`, mientras el flujo vigente usa `assigned` → `arriving` → `in_progress`.
- `livre-cloud/app/crm/router.py` ahora incluye `confirmed`, `assigned`, `arriving` e `in_progress` en los estados operativos con conductor. `assigned` sólo aparece en “Asignar” cuando `assigned_driver_user_id IS NULL`; una aceptación no vuelve a la cola abierta.
- `livre-chatbot/js/app.js` traduce los estados operativos a las vistas del CRM: `assigned` → `WITH_DRIVER_ASSIGNED`, `arriving` → `GOING_TO_CLIENT`, `in_progress` → `GOING_TO_DESTINATION` y `completed`/`done` → `DONE`.
- Se agregó una regresión en `livre-cloud/tests/test_operational_status.py` para garantizar que un viaje `in_progress` asignado aparezca en la pestaña de viajes en curso sin exigir parámetros de estado incorrectos.
- Commits publicados: `livre-cloud` `171be02` (`fix(crm): show accepted trips in progress`) y `livre-chatbot` `0bcc5f6` (`fix(crm): label live trip states`). Ambos fueron enviados a `origin/main`.
- Despliegues verificados online en Railway: `livre-cloud` `40b69c15-7c9e-4d99-b517-33097515db69` y `livre-chatbot` `cbab3fc1-100c-4c62-8751-32f09feba684`. `GET /openapi.json` respondió HTTP 200 y publicó `/crm/travels/live`; el asset JS público contiene `arriving` e `in_progress`.
- Validaciones: `PYTHONPATH=. .venv/bin/pytest -q tests/test_operational_status.py` → `18 passed`; `node --check js/app.js` y `node --test tests/*.test.js` del CRM → `19 passed`; `git diff --check` OK. No se crearon viajes ni se modificaron datos productivos durante la validación.
- El repositorio `livre-chatbot` conserva `.env.local` y `.gitignore` como cambios locales/no versionados; no incluirlos ni exponer su contenido.
- La creación de una segunda cuenta administrativa queda pendiente de definir y autorizar.

## Programación de viajes y migración productiva (2026-10-29)

- La confirmación desde Crear viaje ya recibía `date_programmed`, pero el backend perdía ese dato antes de persistirlo. `_save_trip()` ahora guarda `status` y `scheduled_at`; las fechas sin zona se interpretan como `America/Argentina/Buenos_Aires` y se almacenan en UTC.
- Los viajes programados quedan en `scheduled` y `GET /mobility/driver/trips` los libera a `searching` cuando `scheduled_at <= NOW()`. No se ofrecen antes de la hora programada.
- Se agregó `app/migrations/20261029_scheduled_trips.sql`, aditiva e idempotente, con `scheduled_at TIMESTAMPTZ` e índice `(status, scheduled_at)`.
- El primer despliegue del cambio dejó la migración sin aplicar porque producción tiene `AUTO_MIGRATE=false`. El síntoma real fue `psycopg2.errors.UndefinedColumn: column "scheduled_at" ... does not exist`, afectando confirmar viajes y la lectura de viajes del conductor.
- La migración se aplicó manualmente en la base productiva y quedó registrada en `schema_migrations`. `/health` y `/ready` responden correctamente; posteriormente `/mobility/driver/trips` volvió a responder HTTP 200.
- En Gestión de viajes, el botón `#crm-refresh` ya estaba correctamente conectado a `loadCrmData()`; el endpoint `/crm/travels/live` respondía HTTP 200. El error visible no era del botón, sino del esquema incompleto.
- Commit publicado: `9556b95` (`fix: persist and release scheduled trips`). Tests enfocados: `26 passed`; `git diff --check` OK. No se creó un viaje real durante la verificación.

## Hora del dashboard en Argentina (2026-10-29)

- `livre-chatbot/js/app.js` usa `America/Argentina/Buenos_Aires` explícitamente para la hora de actualización del visor, el último dato GPS, los campos de fecha del historial y valores de fecha genéricos; no depende de la zona horaria del navegador.
- El popup del mapa etiqueta el último dato como `Último dato (Argentina)` y la barra como `Última actualización (Argentina)`.
- Commit publicado en `livre-chatbot`: `75f3b44` (`fix: show dashboard times in Argentina`). `node --check js/app.js` y `git diff --check` OK.
- `livre-chatbot` mantiene cambios locales previos sin versionar en `.env.local`, `errores/` y `.gitignore`; no incluirlos ni exponer su contenido.

## Flujo Livre Ahorro y pagos anticipados (2026-10-30)

- Se agregó el flujo propio de pago anticipado para `service_type_id = "2"` (Livre Ahorro): `POST /elevenlabs/payment-link` valida la cotización/conversación, crea el registro local y solicita a Mercado Pago una preferencia con link de pago; el webhook verifica el estado real antes de completar el pago.
- La migración `app/migrations/20261030_livi_payments.sql` y el router `app/payments/router.py` fueron publicados, pero requieren `MERCADOPAGO_ACCESS_TOKEN` en Railway producción. Sin esa variable, el endpoint responde HTTP 503 `Mercado Pago todavía no está configurado`; no se genera link ni viaje.
- En la prueba real del 2026-10-30, la cotización y `POST /elevenlabs/trip` respondieron HTTP 200, pero la selección de Ahorro llamó a `POST /elevenlabs/payment-link` y recibió HTTP 503. La falla está confirmada en configuración de producción, no en el prompt de ElevenLabs.
- El Access Token debe pertenecer a la cuenta de producción que cobra los viajes de Livre. No documentar el secreto ni usar tokens de otros proyectos. La clave pública no es necesaria para que el backend genere el link.
- Las solicitudes originadas en `panel`/`crm` no requieren pago anticipado; el pago previo sólo aplica a Ahorro en canales de pasajero (`whatsapp`/`passenger`). Esto evita bloquear viajes creados desde el CRM.
- Se mejoraron los errores de cotización para devolver una causa accionable en lugar del mensaje genérico. Commits backend publicados: `43631a2` (flujo de pago), `17e529b` (errores de cotización) y `91bc650` (excepción CRM).
- Pendiente: cargar el Access Token correcto en Railway y repetir una prueba controlada del link, webhook, aprobación y creación posterior del viaje. No afirmar que el cobro está operativo hasta leer de vuelta el link y el estado real del pago.

## Continuidad GPS y diagnóstico del conductor (2026-10-30)

- Se agregaron diagnósticos de heartbeat y sesión de dispositivo para distinguir GPS activo, última coordenada conocida y sesión vencida; no se debe interpretar una coordenada histórica como GPS en tiempo real.
- La app web del conductor conserva el envío de ubicación mientras el panel está abierto según las restricciones del navegador; los cortes en segundo plano de Safari/Brave/Android todavía requieren validación física por dispositivo.
- El frontend de conductor publicó la corrección de estado de dirección: antes de `LLEGUÉ` muestra el retiro del cliente y después muestra el destino. Commit `5f3a57a`; `node --check` y la suite del frontend pasaron 58/58.
- No se crearon viajes ni coordenadas artificiales durante estas verificaciones.

## Diagnóstico pendiente de pronunciación de números en ElevenLabs (2026-10-29)

- En el flujo WhatsApp/LIVI, `livre-cloud/app/voice/elevenlabs_trip.py` recibe y persiste las direcciones como texto; no genera el audio ni transforma la numeración para pronunciarla.
- La respuesta hablada y escrita la genera el agente alojado en ElevenLabs (`agent_3701m0dfvnmmemptqcm2jym9wdvv`). La configuración activa indica repetir la dirección exactamente y pedir que las direcciones se escriban en el chat; las direcciones recibidas por audio deberían rechazarse para evitar errores de calle y numeración.
- La configuración TTS observada en producción usa `eleven_flash_v2_5`, `text_normalisation_type = system_prompt`, velocidad `0.99`, estabilidad `1.0` y `enable_phoneme_tags = true`. No se modificó esa configuración.
- El síntoma reportado es que una dirección como `Estomba 566` aparece correctamente o casi correctamente en texto, pero el número no se entiende bien en el audio. Todavía no está probado si el número se altera en la transcripción del audio entrante, en el texto generado por el agente o únicamente durante la síntesis TTS.
- Para resolverlo hace falta una prueba controlada comparando texto `Estomba 566`, texto `Estomba 89` y audio diciendo `Estomba 566`, registrando texto de WhatsApp, transcripción, texto generado y audio. No cambiar prompts ni TTS hasta separar esas etapas.
- El diagnóstico fue sólo lectura: no se enviaron mensajes, no se generaron viajes y no se modificó el agente de ElevenLabs.
