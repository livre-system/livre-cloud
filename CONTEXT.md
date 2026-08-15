# Livre — Contexto del proyecto

Este documento resume el proyecto completo para poder retomar el trabajo en cualquier momento sin necesidad de reexplicar el contexto desde cero.

---

## ¿Qué es Livre?

Livre es una empresa de transporte de pasajeros. Se le está desarrollando una arquitectura tecnológica propia, completa, que reemplaza herramientas de terceros y centraliza toda la operación bajo una misma infraestructura.

El desarrollo incluye backend, llamadas con IA, dashboard CRM, aplicación para pasajeros, aplicación para conductores y un chatbot multicanal.

---

## Origen del proyecto

El núcleo del sistema está basado en **HASH Cloud**, un chatbot propio ya en producción en otro servidor. Para Livre se replicó y adaptó esa arquitectura con branding y cuentas propias del cliente.

La idea comercial es: si Livre decide adquirir el código, todo ya está desplegado en sus propias cuentas y no requiere ningún cambio. La entrega es inmediata.

---

## Ecosistema completo

| Producto | URL | Descripción |
|---|---|---|
| App de pasajeros | [livre.app](https://livre.app) | Los usuarios piden viajes desde aquí |
| App de conductores | [conductor.livre.app](https://conductor.livre.app) | Interfaz para conductores |
| Dashboard + chatbot admin | [admin.livre.app](https://admin.livre.app) | CRM + chatbot para administradores |

---

## Componentes del sistema

### 1. Livre Cloud (backend)
El núcleo de toda la infraestructura. Centraliza autenticación, contexto, memoria, compilación de conocimiento y comunicación con servicios externos. No implementa el razonamiento del LLM. Alimenta todos los demás productos.

- FastAPI + Postgres
- Autenticación con Google OAuth + JWT
- Mensajes cifrados en reposo con Fernet
- LLM: Gemini principal, Groq como fallback
- Streaming de respuestas
- Compiladores de contexto (Base, Style, User, Hash)

### 2. IA + Llamadas telefónicas
Los usuarios pueden solicitar viajes no solo por mensaje sino también por **llamada telefónica**, atendida automáticamente por IA. El sistema procesa el contexto de cada conversación, registra los datos y los integra al backend.

### 3. Chatbot multicanal
- **WhatsApp**: usuarios pueden pedir viajes por mensaje
- **Llamadas**: atención automática por IA
- **admin.livre.app**: los administradores consultan en tiempo real qué pasa en la empresa — última llamada, estado de viajes, conversaciones activas, eventos

### 4. Dashboard CRM (admin.livre.app)
Panel de administración que concentra toda la información operativa:
- Visualización de llamadas, conversaciones y eventos
- Gestión de viajes y datos operacionales
- Integrado con el núcleo de inteligencia
- Permite a los administradores operar sin depender del desarrollador

### 5. App de pasajeros (livre.app)
Aplicación para que los usuarios soliciten viajes. Conectada al backend vía API.

### 6. App de conductores (conductor.livre.app)
Interfaz para conductores. Recibe viajes asignados y se comunica en tiempo real con la app de pasajeros.

### 7. Comunicación en tiempo real
Las apps de pasajeros y conductores se comunican entre sí en tiempo real a través del backend — asignación de viajes, estado, ubicación.

---

## Stack técnico (Livre Cloud)

| Capa | Tecnología |
|---|---|
| Framework | FastAPI |
| Base de datos | Postgres |
| Autenticación | Google OAuth 2.0 + JWT |
| Cifrado | Fernet (cryptography) |
| LLM principal | Gemini |
| LLM fallback | Groq |
| Servidor | Uvicorn |

### Arquitectura de archivos

```
app/
├── main.py
├── auth/router.py
├── chat/
│   ├── router.py
│   ├── repository.py
│   └── models.py
├── llm/
│   ├── factory.py
│   ├── gemini.py
│   ├── groq.py
│   └── anthropic.py
├── compiler/
│   ├── base_compiler.py
│   └── style_compiler.py
├── voice/
│   ├── router.py
│   ├── twilio_handler.py
│   └── factory.py
└── core/
    ├── config.py
    ├── jwt.py
    ├── encryption.py
    └── database.py
```

---

## Roadmap — 60 días

| Fase | Días | Entregable |
|---|---|---|
| 1. Núcleo + llamadas IA | 1–14 | Backend operativo y atención de llamadas con IA |
| 2. Integraciones + datos | 15–30 | Conectores, memoria, contexto y flujo de datos |
| 3. Dashboard | 31–45 | Panel de administración y operación |
| 4. Apps + producción | 46–60 | App pasajeros, app conductores, integración final |

Las fases pueden solaparse cuando resulte conveniente técnicamente. El cronograma prioriza primero las capacidades de mayor impacto operativo.

**Entregable final:** núcleo de inteligencia operativo, atención de llamadas por IA, integraciones base, dashboard CRM, app de pasajeros y app de conductores — todo sobre una misma arquitectura, desplegado en las cuentas propias del cliente.

---

## Estado actual

- **Livre Cloud** (backend): base completa implementada — autenticación, JWT, cifrado, persistencia de chats, compiladores de contexto, endpoints de chat streaming.
- **Chatbot**: creado y con branding de Livre.
- **Llamadas telefónicas con IA**: implementadas — Twilio integrado, número activo `+1 (608) 351-0209`, webhook configurado, transcripción vía Groq, respuesta vía Gemini, conversación guardada en chat.
- **Próximo paso**: Fase 2 — integraciones, memoria y flujo de datos.

---

## Cómo usar este documento

Pegá este archivo al inicio de un chat nuevo para retomar el trabajo sin reexplicar el contexto. El asistente va a entender de qué se trata el proyecto, qué está hecho y hacia dónde va.
