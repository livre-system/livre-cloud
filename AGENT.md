# AGENT.md — Livre Cloud

Contexto vivo para trabajar en Livre Cloud con Hermes. Este archivo describe el estado operativo actual; puede contener referencias históricas en la sección correspondiente, pero no reemplaza la verificación del código cuando una tarea dependa de un contrato puntual.

## Regla de inicio

- Al comenzar una sesión nueva, leer este `AGENT.md` una sola vez.
- No leer `CONTEXT.md` ni `CONTEXT.MD`.
- No volver a leer este archivo durante la misma sesión salvo pedido explícito o modificación explícita.
- Comparar el contexto con el código real, la base PostgreSQL y los registros de Railway cuando la tarea lo requiera.
- No repetir el contenido completo de este archivo en cada llamada.

## Repositorios

- `livre-cloud`: backend FastAPI, PostgreSQL, API propia y núcleo operativo.
- `livre-chatbot`: CRM/panel web; consume la API de Livre Cloud.
- `livre-pasajeros`: interfaz del pasajero y seguimiento mediante token privado.
- `livre-conductor`: interfaz del conductor, viajes asignados y GPS autenticado.

Ubicación habitual: `/home/calambre/Documentos/Clientes/Livre/`.

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

Aliases históricos o alternativos: `https://livre-chatbot.vercel.app/`, `https://livre-pasajeros.vercel.app/` y `https://livre-system.github.io/`. Verificarlos antes de usarlos como URLs principales.

## Accesos operativos disponibles

- GitHub: `git` y `gh` para revisar repositorios, commits, ramas, issues, PRs y publicar cambios autorizados.
- Railway: CLI `railway` para revisar servicios, deploys, logs y ejecutar verificaciones read-only. No exponer secretos ni cambiar producción sin autorización explícita.
- Vercel: CLI `vercel` para revisar y publicar deployments de los frontends. Verificar siempre el deployment real después de publicar.

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

## Migración del CRM a Railway (2026-10-03)

- `livre-chatbot` quedó preparado para Railway con `package.json` y `server.js` en el commit `ad2175a90d7f22a1573518cedfeb687d18f1a3ee` (`chore: prepare CRM for Railway`), publicado en `origin/main`.
- Se verificó `node --check server.js` y `npm test`: 4 tests pasaron.
- No se hizo deploy, no se ejecutó SQL, no se generó APK y no se modificó Vercel.
- `livre-cloud/app/main.py` ya permite los orígenes Railway conocidos, pero todavía no incluye el CRM Railway.
- Para completar CORS se necesita conocer el dominio público Railway definitivo del servicio CRM, como origen HTTPS exacto. No modificar `allow_origins` hasta contar con ese dato.

## Diagnóstico GPS nativo en Redmi confirmado (2026-10-14)

- La APK diagnóstica activa es `livre-conductor/dist/app-debug.apk`, paquete `com.livre.conductor.diagnostic`; no se generó ni reinstaló otra APK durante esta verificación.
- En el Redmi, Google Play Services está disponible y Fused acepta solicitudes `PRIORITY_HIGH_ACCURACY`; una solicitud aceptada no garantiza que exista un fix para entregar.
- La señal GNSS puede quedar temporalmente sin fix: el proveedor GPS llegó a informar `satellites=0`, `maxCn0=0`, `meanCn0=0`, y Maps tampoco mostró inicialmente una ubicación actual.
- La causa no es un rechazo de permisos ni un fallo permanente de Fused. El sistema tenía ubicación habilitada (`location_mode=3`) y permisos concedidos; Maps llegó a advertir que la Precisión de la ubicación debía activarse.
- La prueba repetida del 2026-10-14 confirmó recuperación sin modificar código, APK ni producción: Fused activó el fallback GPS, luego entregó callbacks con precisión `23.6 m` y `14.616 m`, y la app obtuvo `POST /mobility/driver/location HTTP 200` en dos ocasiones.
- La pantalla de Livre mostró marcador verde actual y estado `Disponible` después de recuperar señal.
- Comparación importante: la sesión anterior también tuvo callback válido de `7.1 m` y luego `lose location`; el comportamiento es intermitente y depende de que el Redmi consiga un fix GNSS/Fused, no de que la solicitud sea aceptada.
- Para nuevos diagnósticos de GPS, primero revisar Google Play Services, Maps, `dumpsys location` y Logcat; no generar APK ni cambiar configuración/producción hasta separar señal, estado del dispositivo y código.
