# Livre Cloud

Backend del ecosistema Livre.

## Responsabilidad

Livre Cloud es el núcleo de infraestructura del ecosistema Livre. Centraliza autenticación, contexto, memoria, compilación de conocimiento, operación de viajes y comunicación con servicios externos.

Alimenta los productos de la plataforma: aplicación para pasajeros, interfaz para conductores, dashboard CRM operativo y asistentes conversacionales integrados con WhatsApp y LIVI.

Livre Cloud ejecuta la lógica de negocio y las integraciones. El razonamiento del LLM permanece en el agente correspondiente.

---

## Arquitectura

```text
Pasajero
  ↓
WhatsApp
  ↓
LIVI / ElevenLabs
  ↓
Livre Cloud (Railway)
  ↓
livi_trips
  ↓
MAGIIS Web
  ↓
Operación del viaje
```

Para el panel:

```text
Usuario del panel
  ↓
JWT
  ↓
Livre Cloud
  ↓
Contexto / memoria / datos operativos
```

Livre Cloud debe seguir siendo el core. MAGIIS es una dependencia externa y ElevenLabs es el canal/agente conversacional. No acoplar directamente ElevenLabs con MAGIIS.

---

## Repositorios y despliegue

| Componente | Ubicación |
|---|---|
| Backend | FastAPI |
| Backend hosting | Railway |
| Frontend | Vercel |
| Base de datos | PostgreSQL |
| Embeddings | OpenAI `text-embedding-3-small`, 1536 dimensiones |

URL pública del backend:

```text
https://livre-cloud-production.up.railway.app
```

---

## Dependencias

Consultar `requirements.txt` como fuente de verdad para las versiones instaladas.

Instalar:

```bash
pip install -r requirements.txt
```

---

## Variables de entorno

Las credenciales y secretos deben existir únicamente como variables de entorno/secrets. No deben copiarse al código, README, Git ni logs.

Variables relevantes:

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | Conexión a PostgreSQL |
| `JWT_SECRET` | Clave para firmar tokens JWT |
| `JWT_ALGORITHM` | Algoritmo JWT, normalmente `HS256` |
| `JWT_EXPIRE_MINUTES` | Duración del token |
| `CREDENTIALS_SECRET` | Clave Fernet para cifrado |
| `GROQ_API_KEY` | API key de Groq |
| `TWILIO_ACCOUNT_SID` | Account SID de Twilio |
| `TWILIO_AUTH_TOKEN` | Auth Token de Twilio |
| `TWILIO_PHONE_NUMBER` | Número de Twilio |
| `BASE_URL` | URL pública del backend |
| `MAGIIS_WEB_USERNAME` | Usuario de MAGIIS Web |
| `MAGIIS_WEB_PASSWORD` | Password de MAGIIS Web |
| `MAGIIS_WEB_FLOW_ENABLED` | Habilita el flujo MAGIIS Web |
| `MAGIIS_WEB_CREATE_ENABLED` | Habilita creación real de viajes |

No usar ni reintroducir las variables de Google OAuth eliminadas.

Generar una clave Fernet válida:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Autenticación

Google OAuth fue eliminado.

El panel utiliza autenticación propia:

```text
Usuario + contraseña
        ↓
POST /auth/login
        ↓
JWT
        ↓
livre_users.id
```

La identidad interna del usuario del panel es:

```text
livre_users.id
```

El `sub` del JWT contiene ese identificador.

Endpoints principales:

```text
POST /auth/register
POST /auth/login
GET  /auth/me
```

No usar email como identidad interna ni crear cuentas del panel automáticamente para pasajeros de WhatsApp.

---

## Identidades

Existen identidades diferentes y no deben mezclarse:

```text
PANEL
livre_users.id
  ↓
JWT
  ↓
usuario autenticado del panel
```

```text
WHATSAPP
system__caller_id
  ↓
LIVI / ElevenLabs
  ↓
livi_trips.whatsapp_user
  ↓
pasajero que originó el viaje
```

Además:

```text
conversation_id
  ↓
conversación concreta de LIVI
```

`system__caller_id` puede ser un WhatsApp User ID / BSUID y no necesariamente un número telefónico tradicional.

No usar `system__called_number` para identificar al pasajero.

No crear `user_whatsapp_mappings` ni vincular automáticamente un pasajero de WhatsApp con `livre_users`.

---

## Base de datos

Las tablas existentes deben preservarse. No borrar ni recrear tablas para resolver cambios puntuales.

Tablas relevantes:

| Tabla | Propósito |
|---|---|
| `livre_users` | Usuarios autenticados del panel |
| `chats` | Conversaciones del panel |
| `chat_messages` | Mensajes del panel |
| `livi_trips` | Estado operacional de viajes recibidos desde LIVI |
| tablas de memoria/embeddings | Contexto persistente |

`livi_trips` utiliza, entre otros:

```text
id
conversation_id
origen
destino
whatsapp_user
tool_call_id
status
created_at
updated_at
```

Para consultas operativas, `livi_trips` es la fuente de verdad.

El último viaje se determina por:

```sql
ORDER BY created_at DESC
```

No usar búsqueda semántica ni `Chat log` para decidir cuál es el último viaje.

---

## Memoria y embeddings

Modelo:

```text
text-embedding-3-small
1536 dimensiones
```

La memoria y los embeddings son contexto, no estado operacional.

Para el panel:

```text
JWT
 ↓
require_auth
 ↓
livre_users.id
 ↓
memoria/contexto del usuario
```

Los flujos históricos de LIVI todavía pueden utilizar temporalmente:

```text
elevenlabs_voice
```

hasta definir una identidad persistente específica para el pasajero de WhatsApp.

No migrar ni borrar memorias existentes sin una decisión explícita y análisis previo.

---

## LIVI / ElevenLabs

LIVI gestiona pedidos de viaje por WhatsApp.

Flujo normal:

```text
Saludo
  ↓
Origen escrito
  ↓
Confirmación de origen
  ↓
Destino escrito
  ↓
Confirmación de destino
  ↓
cotizar_viaje
  ↓
Precio
  ↓
Confirmación explícita del pasajero
  ↓
registrar_viaje
  ↓
Confirmación
  ↓
end_call
```

Las direcciones deben ser escritas por el pasajero.

No procesar direcciones recibidas por audio.

Si llega una dirección por audio:

```text
Para evitar confusiones con la calle y la numeración, escribí la dirección completa en un mensaje 📍
```

No inventar, completar, corregir ni reinterpretar una dirección escrita y confirmada por el pasajero.

### Identificador WhatsApp

La herramienta `registrar_viaje` recibe:

```text
whatsapp_user = system__caller_id
```

Endpoint:

```text
POST /elevenlabs/trip
```

Archivo:

```text
app/voice/elevenlabs_trip.py
```

---

## Cotización

La cotización está separada de la creación real del viaje.

Herramienta:

```text
cotizar_viaje
```

Endpoint:

```text
POST /elevenlabs/quote
```

Flujo:

```text
cotizar_viaje
  ↓
/elevenlabs/quote
  ↓
MAGIIS simulateAll
  ↓
precio
  ↓
confirmación explícita del pasajero
  ↓
registrar_viaje
```

`cotizar_viaje` nunca debe crear un viaje real.

Si la cotización falla, no inventar un precio y no ejecutar `registrar_viaje`.

---

## Registro de viajes

Endpoint:

```text
POST /elevenlabs/trip
```

Herramienta:

```text
registrar_viaje
```

El viaje se guarda en:

```text
livi_trips
```

Debe mantenerse la idempotencia:

```text
1 conversation_id = 1 viaje
```

Un retry del webhook no debe crear duplicados.

El estado inicial es:

```text
pending
```

El registro real ocurre únicamente después de:

```text
cotización válida
  +
confirmación explícita del pasajero
```

No afirmar éxito si la herramienta no confirmó la creación.

---

## Cancelación

Herramienta:

```text
cancelar_viaje
```

Endpoint:

```text
POST /elevenlabs/cancel
```

Flujo:

```text
cancelar_viaje
  ↓
/elevenlabs/cancel
  ↓
último viaje activo del pasajero
  ↓
travel_id real de MAGIIS
  ↓
PUT /carriers/2421/travels/{travel_id}/cancel
  ↓
actualizar livi_trips
  ↓
confirmación
  ↓
end_call
```

La cancelación usa el `travel_id` real de MAGIIS, no el ID local de `livi_trips`.

Si MAGIIS falla, no afirmar que el viaje fue cancelado.

La cancelación tiene prioridad sobre cotización y registro:

```text
cancelar intención
  ↓
cancelar_viaje
```

No ejecutar `registrar_viaje` ni `cotizar_viaje` durante una cancelación.

---

## MAGIIS

La integración vigente utiliza exclusivamente MAGIIS Web:

```text
https://api.apps.magiis.com
```

La API pública:

```text
https://api.public.magiis.com
```

queda fuera del flujo vigente y no debe mezclarse con la integración Web.

### Contrato Web utilizado

```text
POST /auth/login
POST /carriers/2421/travels/simulateAll
POST /carriers/2421/travels
PUT  /carriers/2421/travels/{travel_id}/cancel
```

Carrier:

```text
2421
```

### Livre Premium

La cotización y creación utilizan:

```text
vehicleTypeId = 2
transportTypeId = 2
```

Esto corresponde a Livre Premium.

### Direcciones

El texto confirmado por el pasajero es la autoridad para la dirección textual.

MAGIIS aporta:

```text
latitude
longitude
placeId
```

No utilizar el `shortName` o `formattedAddress` de MAGIIS para reemplazar la dirección confirmada.

Ejemplo:

```text
Pasajero:
Dorrego 25

MAGIIS:
Dorrego 17
```

Resultado correcto:

```text
destination.shortName = "dorrego 25"
```

mientras las coordenadas y `placeId` provienen de MAGIIS.

Este comportamiento está cubierto por:

```text
tests/test_magiis_web_create_travel.py
```

y los tests relevantes fueron validados con 4 casos pasando.

---

## Geocodificación

Flujo conceptual:

```text
Dirección escrita
  ↓
geocoding
  ↓
latitude / longitude
  ↓
MAGIIS
```

El pasajero no debe proporcionar coordenadas.

Nominatim/OpenStreetMap se utiliza como fallback de geocodificación.

Un problema histórico con `Dorrego 25` no estaba en Nominatim, sino en el `shortName` devuelto por MAGIIS. La implementación actual conserva el texto original del pasajero.

---

## Asistente del panel

El chatbot del panel es una interfaz autenticada para el dueño y personal autorizado.

Puede consultar:

- viajes recibidos desde WhatsApp;
- conversaciones y eventos;
- información operativa;
- contexto y memoria disponibles.

Las consultas operativas deben usar datos estructurados.

No debe inventar:

- precios;
- estados;
- ubicaciones;
- disponibilidad;
- horarios;
- pasajeros;
- conductores;
- datos de viajes.

Toda la información económica se interpreta como pesos argentinos (ARS). No convertir importes automáticamente a dólares u otra moneda.

---

## Endpoints principales

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/health` | No | Estado del servidor |
| POST | `/auth/register` | No | Registro de usuario |
| POST | `/auth/login` | No | Login con usuario/contraseña |
| GET | `/auth/me` | Bearer JWT | Identidad autenticada |
| GET | `/chat/list` | Bearer JWT | Lista chats |
| POST | `/chat/new` | Bearer JWT | Crea chat |
| GET | `/chat/{id}/messages` | Bearer JWT | Historial |
| PATCH | `/chat/{id}/title` | Bearer JWT | Renombra chat |
| DELETE | `/chat/{id}` | Bearer JWT | Elimina chat |
| POST | `/chat` | Bearer JWT | Respuesta no-streaming |
| POST | `/chat/stream` | Bearer JWT | Respuesta streaming |
| GET | `/compiler/base` | Bearer JWT | Construye BaseContext |
| GET | `/compiler/style` | Bearer JWT | Construye StyleContext |
| POST | `/elevenlabs/quote` | ElevenLabs | Cotiza viaje |
| POST | `/elevenlabs/trip` | ElevenLabs | Registra viaje |
| POST | `/elevenlabs/cancel` | ElevenLabs | Cancela viaje |
| POST | `/voice/incoming` | Twilio | Llamada entrante |
| POST | `/voice/respond` | Twilio | Procesamiento de llamada |
| GET | `/docs` | No | Documentación interactiva |

---

## Flujo operativo

### Pedido normal

```text
WhatsApp
  ↓
LIVI / ElevenLabs
  ↓
origen escrito
  ↓
confirmación
  ↓
destino escrito
  ↓
confirmación
  ↓
cotizar_viaje
  ↓
/elevenlabs/quote
  ↓
MAGIIS simulateAll
  ↓
precio Livre Premium
  ↓
confirmación del pasajero
  ↓
registrar_viaje
  ↓
/elevenlabs/trip
  ↓
MAGIIS Web
  ↓
POST /carriers/2421/travels
  ↓
viaje real
  ↓
confirmación
  ↓
end_call
```

### Cancelación

```text
WhatsApp
  ↓
LIVI detecta cancelación
  ↓
cancelar_viaje
  ↓
/elevenlabs/cancel
  ↓
buscar viaje activo
  ↓
travel_id MAGIIS
  ↓
PUT /carriers/2421/travels/{id}/cancel
  ↓
actualizar estado
  ↓
confirmación
  ↓
end_call
```

---

## Reglas de veracidad

Las herramientas son la fuente de verdad para sus operaciones.

Solo afirmar:

```text
cotización existente
```

si `cotizar_viaje` devolvió un precio válido.

Solo afirmar:

```text
viaje registrado
```

si `registrar_viaje` confirmó éxito.

Solo afirmar:

```text
viaje cancelado
```

si `cancelar_viaje` confirmó éxito.

Nunca inventar precios, conductores, horarios, disponibilidad, estados, direcciones ni resultados de herramientas.

---

## Desarrollo local

Primera instalación:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Ejecutar:

```bash
uvicorn app.main:app --reload
```

---

## Estructura principal

```text
app/
├── main.py
├── auth/
│   └── router.py
├── chat/
│   ├── router.py
│   ├── repository.py
│   └── models.py
├── context/
│   ├── provider.py
│   └── router.py
├── llm/
│   ├── factory.py
│   ├── groq.py
│   ├── gemini.py
│   └── anthropic.py
├── compiler/
│   ├── base_compiler.py
│   └── style_compiler.py
├── voice/
│   ├── router.py
│   ├── elevenlabs.py
│   ├── elevenlabs_trip.py
│   ├── elevenlabs_postcall.py
│   ├── elevenlabs_memory.py
│   └── elevenlabs_debug.py
├── integrations/
│   └── magiis_web.py
└── core/
    ├── config.py
    ├── jwt.py
    ├── encryption.py
    └── database.py
```

---

## Tests y validaciones

Para cambios puntuales:

```bash
python -m compileall app
git diff --check
```

Los tests específicos deben ejecutarse cuando las dependencias del entorno estén disponibles.

Para MAGIIS:

```text
tests/test_magiis_web_create_travel.py
```

El test de preservación de dirección valida que el `shortName` incorrecto devuelto por MAGIIS no reemplace el texto original confirmado por el pasajero.

---

## Reglas de desarrollo

- No reemplazar `main.py` completo.
- No reconstruir la arquitectura existente.
- No borrar tablas.
- No borrar memorias ni embeddings.
- No reintroducir Google OAuth.
- No usar WhatsApp como `livre_users.id`.
- No crear usuarios del panel para pasajeros de WhatsApp.
- No mezclar contratos de MAGIIS Web y API pública.
- No hacer refactors generales para resolver problemas puntuales.
- Investigar primero y cambiar lo mínimo necesario.
- Mantener cambios aislados, reversibles y compatibles.
- No hacer commit ni push salvo que corresponda a la tarea.
- No exponer credenciales, tokens, passwords ni secretos.
- Actualizar `AGENT.md` únicamente ante decisiones o cambios permanentes relevantes.

---

## Estado operativo

El flujo base de viajes validado incluye:

```text
captura de origen
→ confirmación
→ captura de destino
→ confirmación
→ cotización
→ confirmación de precio
→ creación real
→ confirmación
```

También está validada la cancelación mediante `cancelar_viaje`.

La integración histórica con MAGIIS Web funcionaba con Livre Premium

No asumir que hay un bug activo en estas partes sin una prueba nueva que lo demuestre.

Los próximos cambios deben tratarse como tareas nuevas y aisladas.
