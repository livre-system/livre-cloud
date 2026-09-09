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

Integración prevista:

``` text
ElevenLabs → livi_trips → app/integrations/magiis.py → MAGIIS API
```

Credenciales configuradas en Railway mediante variables de entorno:

``` text
MAGIIS_USERNAME
MAGIIS_PASSWORD
MAGIIS_APP_NAME
```

Configuración informada:

``` text
MAGIIS_USERNAME=livre-system
MAGIIS_APP_NAME=livre-system
```

No exponer ni repetir contraseñas reales.

Passenger técnico:

``` text
Livre Cloud
passengerUserId=436133
```

No crear pasajeros dinámicamente.

Auth:

``` text
POST https://api.public.magiis.com/auth/login
```

Crear viaje:

``` text
POST https://api.public.magiis.com/trip/create
```

En pruebas se usó `generatedTravel=0` para no generar un viaje real.

MAGIIS queda en pausa hasta estabilizar identidad/LIVI.

## Estado actual

Completado: - Google OAuth eliminado. - Registro/login propio. - JWT
basado en `livre_users.id`. - Tabla `livre_users`. - Frontend sin
Google. - Login por usuario/contraseña en proceso/finalización. - Groq
funcionando. - Modelo Groq: `openai/gpt-oss-20b`. - `registrar_viaje`. -
`conversation_id`. - `whatsapp_user`. - Variable ElevenLabs:
`system__caller_id`.

Pendiente inmediato: 1. Confirmar que `whatsapp_user` llega realmente a
`/elevenlabs/trip`. 2. Confirmar que se guarda en `livi_trips`. 3.
Corregir el uso de `elevenlabs_voice` en el contexto de memoria del
panel. 4. Definir identidad persistente del pasajero WhatsApp para
memoria/post-call. 5. Conectar esa identidad con memoria LIVI. 6.
Retomar MAGIIS.

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

## Último viaje desde el panel

El router activo de `/chat/stream` es `app/context/router.py`, incluido antes
que `app/chat/router.py` en `app/main.py`.

Las identidades no son equivalentes:

``` text
PANEL: JWT / require_auth → livre_users.id
VIAJE: WhatsApp caller ID → LIVI → livi_trips.whatsapp_user
```

`livi_trips.whatsapp_user` identifica el número de WhatsApp que originó el
viaje. No identifica al usuario autenticado del panel y no debe usarse para
inferir `livre_users.id`.

Para una consulta del panel sobre viajes, solo se pueden devolver viajes con
una relación explícita y aprobada en datos operativos, por ejemplo un
`livi_trips.user_id` asignado deliberadamente. No se debe crear ni asumir una
relación mediante `user_whatsapp_mappings` sin una decisión de negocio previa.
Si no existe una relación explícita, no devolver viajes de WhatsApp al panel.

Las consultas de "último viaje" no deben usar búsqueda semántica para decidir
el viaje. La fuente operacional, cuando exista una relación explícita, es
`livi_trips` ordenada por `created_at DESC`. Los documentos históricos `Chat
log` no son fuente de verdad operacional.

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
