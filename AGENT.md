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
  → livi_trips + tracking_token + assigned_driver_user_id
  → livre-conductor /mobility/driver/trips
  → estado y GPS autenticados
  → livre-pasajeros /mobility/tracking/{tracking_token}
```

El pedido de prueba valida Bahía Blanca y alrededores y asigna un conductor activo de `livre_driver_accounts`. La ubicación del conductor se guarda en `livi_trips`; nunca debe venir del navegador del pasajero.

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
