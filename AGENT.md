# AGENT.md --- Livre Cloud / Viaja Livre

## Objetivo

Contexto operativo para continuar el desarrollo de Livre Cloud / Viaja
Livre con Hermes. Prioridad: continuidad, cambios mínimos y no romper
funcionalidades existentes.

## Arquitectura

``` text
Pasajero → WhatsApp → LIVI / ElevenLabs → Livre Cloud (Railway) → livi_trips → MAGIIS
```

Frontend: Vercel. Backend: FastAPI en Railway. Memoria/embeddings:
PostgreSQL, `text-embedding-3-small`, 1536 dimensiones.

No reemplazar `main.py` completo. No reconstruir el sistema. Hacer
cambios aislados y mínimos.

## Identidades: NO mezclar

### Usuario del panel

`livre_users.id` es la identidad interna del usuario del panel y el
`sub` del JWT.

Login final: - Usuario: `livreuser` - Contraseña de prueba: `livre123`

No usar Google. No usar email como credencial de login.

### Cliente/pasajero de WhatsApp

Es la persona que escribe al WhatsApp de LIVI. Puede no tener usuario en
el panel.

El identificador de WhatsApp sirve para saber qué pasajero originó un
viaje. No debe convertirse en `livre_users.id`.

Modelo:

``` text
PANEL: livre_users.id → JWT → memoria del usuario del panel

WHATSAPP: caller ID → LIVI → livi_trips.whatsapp_user
```

No crear cuentas de panel automáticamente para pasajeros de WhatsApp.

## Autenticación

Endpoints:

``` text
POST /auth/register
POST /auth/login
GET /auth/me
```

Google OAuth fue eliminado.

JWT:

``` text
livre_users.id
```

como identidad interna.

`app/core/jwt.py` permanece agnóstico del proveedor de autenticación.

## Base de datos

`app/core/database.py` crea `livre_users` dentro de `init_db()` con
`CREATE TABLE IF NOT EXISTS`.

Esquema informado:

``` sql
CREATE TABLE IF NOT EXISTS livre_users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_livre_users_email
ON livre_users(email);
```

Importante: el login deseado es username + password. Si el esquema
todavía conserva `email`, cualquier adaptación debe ser mínima y sin
borrar usuarios/datos.

No borrar ni recrear tablas existentes.

## Frontend

`js/app.js` eliminó Google OAuth y agregó login/registro propio.

La interfaz final debe mostrar:

``` text
Usuario
Contraseña
```

No Email.

No tocar memoria, ElevenLabs ni MAGIIS al hacer cambios de
autenticación.

## Memoria

Las memorias y embeddings existentes deben preservarse.

Modelo:

``` text
text-embedding-3-small
1536 dimensiones
```

Problema detectado: algunos flujos todavía usan `elevenlabs_voice`.

Los logs mostraron:

``` text
[MEMORY DEBUG] BUILD START memory_user=elevenlabs_voice
```

Esto es incorrecto para usuarios autenticados del panel.

Para el panel debe ser:

``` text
JWT → require_auth → livre_users.id → memoria
```

No borrar ni migrar memorias existentes sin analizarlo primero.

`MEMORY_USER_ID = "elevenlabs_voice"` puede seguir existiendo
temporalmente para LIVI hasta definir la identidad del pasajero.

## ElevenLabs / LIVI

Endpoint:

``` text
POST /elevenlabs/trip
```

Archivo:

``` text
app/voice/elevenlabs_trip.py
```

`livi_trips` ya contiene/usa:

``` text
conversation_id
origen
destino
whatsapp_user
tool_call_id
status
created_at
updated_at
```

Herramienta ElevenLabs:

``` text
registrar_viaje
```

URL:

``` text
https://livre-cloud-production.up.railway.app/elevenlabs/trip
```

### WhatsApp

En la herramienta `registrar_viaje` se está configurando:

``` text
Identificador: whatsapp_user
Tipo de valor: Variable Dinámica
Nombre de variable: system__caller_id
```

Para WhatsApp, `system__caller_id` identifica al usuario que inició la
conversación. No usar `system__called_number`, que corresponde al número
llamado.

`system__caller_id` puede ser un WhatsApp User ID / BSUID y no
necesariamente un teléfono con formato `+549...`.

No asumir que es un número telefónico tradicional.

## Prompt de LIVI

LIVI tiene una única función: tomar pedidos de viaje.

No es un asistente general.

Saludo inicial:

``` text
¡Hola! 👋 Soy LIVI, el asistente de Viaja Livre 🚕. ¿Desde dónde salís? Escribí la dirección completa en un mensaje 📍
```

Nunca debe empezar con: - ¿En qué puedo ayudarte? - ¿Cómo puedo
ayudarte? - ¿Qué necesitás?

Flujo:

``` text
Saludo
→ origen escrito
→ confirmación
→ destino escrito
→ confirmación
→ registrar_viaje
→ confirmación
→ end_call
```

Las direcciones deben ser escritas por el usuario. No procesar
direcciones recibidas por audio.

Si recibe una dirección por audio:

``` text
Para evitar confusiones con la calle y la numeración, escribí la dirección completa en un mensaje 📍
```

Cuando origen y destino estén confirmados, `registrar_viaje` es
obligatorio.

Éxito:

``` text
Perfecto, tu viaje está registrado. 🚕 ¡Buen viaje!
```

Después ejecutar `end_call`.

No afirmar que el viaje está registrado si la herramienta no devolvió
éxito.

## Post-call / memoria LIVI

Archivos:

``` text
app/voice/elevenlabs_postcall.py
app/voice/elevenlabs_memory.py
app/voice/elevenlabs_debug.py
```

Varios todavía usan:

``` text
ELEVENLABS_SYSTEM_USER = "elevenlabs_voice"
```

No modificar esto improvisadamente. Primero definir correctamente la
identidad persistente del pasajero de WhatsApp.

No mezclar pasajero WhatsApp con usuario del panel.

## MAGIIS

Integración vigente:

``` text
ElevenLabs → livi_trips → app/integrations/magiis_web.py → MAGIIS Web
```

La API pública (`api.public.magiis.com`) queda descartada completamente. No
usar `app/integrations/magiis.py`, `generatedTravel` ni sus credenciales para
este flujo.

MAGIIS Web validado:

- Login Web HTTP 200.
- `simulateAll` HTTP 200 con `transportTypeId=2` disponible.
- Creación Web HTTP 200 con `travelId`; última prueba: `Dorrego 25 → Vieytes 968`,
  `travelId=6672893`, estado inicial `SEARCHING_DRIVER`.
- No ejecutar audit ni cancel automáticamente.
- La web realiza autocomplete y luego consulta `GET /places/getPlace?placeId=...`.
- `simulateAll` recibe la ubicación ya resuelta: coordenadas, `placeId` y
  `shortName`; no recibe una dirección textual cruda.
- La resolución conserva de `getPlace` únicamente `latitude`, `longitude` y
  `placeId`. El `shortName` de MAGIIS no reemplaza la dirección confirmada por
  el pasajero.
- Al crear el viaje, el payload envía el texto original confirmado. Ejemplo:
  `destino="dorrego 25"` → `destination.shortName="dorrego 25"`.
- `transportTypeId`/`vehicleTypeId` queda fijado en `2` para Livre Ahorro.
- Test preparado en `tests/test_magiis_web_create_travel.py` y validado con 4
  tests pasando.

Variables de Railway (no exponer valores):

``` text
MAGIIS_WEB_USERNAME
MAGIIS_WEB_PASSWORD
MAGIIS_WEB_FLOW_ENABLED
MAGIIS_WEB_CREATE_ENABLED
```

No exponer ni repetir contraseñas, tokens ni respuestas sensibles.


## Estado actual

Completado: - Google OAuth eliminado. - Registro/login propio. - JWT
basado en `livre_users.id`. - Tabla `livre_users`. - Frontend sin
Google. - Login por usuario/contraseña en proceso/finalización. - Groq
funcionando. - Modelo Groq: `openai/gpt-oss-20b`. - `registrar_viaje`. -
`conversation_id`. - `whatsapp_user`. - Variable ElevenLabs:
`system__caller_id`.

Pendiente inmediato: 1. Probar el webhook con `MAGIIS_WEB_FLOW_ENABLED=1` sin creación real. 2. Resolver el bloqueo `REQUEST_DENIED` de Google Geocoding y confirmar `placeId` compatibles para Bahía Blanca. 3. Revisar la cotización persistida en `livi_trips`. 4. Habilitar `MAGIIS_WEB_CREATE_ENABLED=1` solo con autorización y prueba controlada. 5. Mantener separadas las identidades de panel y pasajero WhatsApp.

## Reglas para Hermes

NO: - reemplazar `main.py` completo - rehacer arquitectura - borrar
tablas - borrar embeddings/memorias - cambiar `text-embedding-3-small` -
reintroducir Google OAuth - usar email como identidad interna - usar
WhatsApp como `livre_users.id` - crear usuarios de panel para pasajeros
WhatsApp - tocar MAGIIS mientras se resuelve identidad/memoria -
modificar muchos subsistemas a la vez

SÍ: - inspeccionar primero - cambiar lo mínimo - preservar
compatibilidad - usar `livre_users.id` para usuarios del panel -
mantener pasajero WhatsApp separado - verificar logs - informar
exactamente archivos modificados

## Próximo objetivo

Primero:

``` text
WhatsApp
→ ElevenLabs
→ system__caller_id
→ registrar_viaje
→ livi_trips.whatsapp_user
```

Luego:

``` text
WhatsApp caller ID
→ identidad persistente del pasajero LIVI
→ memoria/post-call del pasajero
```

Sin relacionarlo con `livre_users`.

## Regla crítica

Distinguir siempre:

1.  `livre_users.id` = usuario del panel
2.  `system__caller_id` = pasajero que escribe por WhatsApp
3.  `conversation_id` = conversación concreta de LIVI
4.  `whatsapp_user` = identificador del pasajero guardado en el viaje

No asumir que estos valores son intercambiables.

## Asistente del panel

El panel y su chatbot son la misma herramienta para el dueño y el personal
autorizado. El usuario del panel solo inicia sesión y controla el acceso; no
representa a un pasajero ni es dueño de los viajes.

El contexto general de la IA del panel se define en
`app/context/provider.py`. Debe explicar que el asistente consulta la
operación general de Viaja Livre, puede informar sobre viajes recibidos desde
WhatsApp y debe usar solo los datos operativos disponibles, sin inventar ni
mezclar identidades.

Toda la información económica de la empresa se interpreta siempre como pesos
argentinos (ARS). La IA no debe convertir precios, tarifas, saldos, importes ni
límites a dólares u otra moneda, aunque el usuario cargue los datos sin indicar
la moneda.

## Último viaje desde el panel

El router activo de `/chat/stream` es `app/context/router.py`, incluido antes
que `app/chat/router.py` en `app/main.py`.

Las identidades no son equivalentes:

``` text
PANEL: JWT / require_auth → livre_users.id (solo inicio de sesión)
VIAJE: WhatsApp caller ID → LIVI → livi_trips.whatsapp_user
```

El número de WhatsApp identifica únicamente quién solicitó el viaje y sirve
para conservar el pedido junto con origen, destino y datos operativos. No
identifica al usuario autenticado del panel y nunca debe usarse para inferir
`livre_users.id`. No crear `user_whatsapp_mappings` ni cuentas de panel para
estos pasajeros.

El panel es una interfaz autenticada para consultar la información general de
los viajes recibidos desde LIVI. Sus consultas operativas deben leer
`livi_trips` sin filtrar por `livre_users.id` ni exigir una relación entre el
panel y el pasajero de WhatsApp. El usuario del panel solo controla el acceso
a la interfaz.

Las consultas de "último viaje" no deben usar búsqueda semántica para decidir
el viaje. La fuente operacional es `livi_trips` ordenada por `created_at DESC`.
Los documentos históricos `Chat log` no son fuente de verdad operacional.

## Diagnóstico de producción: separación de identidades

Verificación read-only realizada contra PostgreSQL de producción mediante el
TCP Proxy temporal de Railway. No se ejecutaron INSERT, UPDATE, DELETE,
ALTER ni migraciones.

El usuario de panel `livreuser` tiene:

``` text
livre_users.id = 4d96ce43-d0c1-41fb-9700-181cf26ca481
```

No existe actualmente un mapping en `user_whatsapp_mappings` para ese usuario.
Eso no significa que `5491178240367` pertenezca al usuario del panel: es el
caller ID de WhatsApp del viaje y puede no tener ninguna relación con
`livre_users`.

En producción:

- `livi_trips.id=7` tiene `whatsapp_user=''` y `user_id=NULL` porque fue creado
  antes del fix de extracción.
- Los trips históricos 1 a 7 quedaron con `whatsapp_user=''`.
- `livi_trips.id=8`, creado después del fix, tiene
  `whatsapp_user='5491178240367'` y `user_id=NULL`.
- El código actual de `app/voice/elevenlabs_trip.py` extrae
  `body.whatsapp_user` y conserva ese valor en `livi_trips.whatsapp_user`.
- El webhook post-call recibe el identificador, pero no actualiza
  `livi_trips`.

No crear `user_whatsapp_mappings` para reparar estos viajes hasta que exista
una decisión de negocio explícita que defina una relación panel↔WhatsApp. El
TCP Proxy público usado para esta investigación es temporal y debe
desactivarse al finalizar.
