# Livre Cloud

Backend del ecosistema Livre.

## Responsabilidad

Livre Cloud es el núcleo de infraestructura del ecosistema Livre. Centraliza los servicios de autenticación, contexto, memoria, compilación de conocimiento y comunicación con servicios externos.

Alimenta el conjunto completo de productos de la plataforma: una aplicación para que los usuarios soliciten viajes, una aplicación e interfaz para conductores, un dashboard CRM que concentra la información operativa de la empresa, y un chatbot multicanal que permite a los administradores consultar en tiempo real el estado de la operación — incluyendo llamadas, conversaciones y eventos — y a los usuarios solicitar viajes tanto por mensaje como por llamada telefónica mediante IA, integrado con WhatsApp.

No implementa el razonamiento del LLM.

---

## Dependencias

| Paquete | Versión |
|---|---|
| fastapi | 0.139.0 |
| uvicorn | 0.51.0 |
| authlib | 1.7.2 |
| httpx | 0.28.1 |
| python-dotenv | 1.2.2 |
| itsdangerous | 2.2.0 |
| PyJWT | 2.7.0 |
| google-api-python-client | 2.169.0 |
| google-auth | 2.40.3 |
| google-auth-oauthlib | 1.2.1 |
| cryptography | >=45.0.1 |
| psycopg2-binary | 2.9.10 |

Instalar:

```bash
pip install -r requirements.txt
```

---

## Variables de entorno

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | Conexión a Postgres |
| `GOOGLE_CLIENT_ID` | Client ID del proyecto en Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | Client Secret del proyecto en Google Cloud Console |
| `GOOGLE_REDIRECT_URI` | URI de callback registrada en Google (`/auth/callback`) |
| `SESSION_SECRET` | Clave secreta para firmar la sesión de OAuth |
| `JWT_SECRET` | Clave secreta para firmar los tokens JWT |
| `JWT_ALGORITHM` | Algoritmo JWT (default: `HS256`) |
| `JWT_EXPIRE_MINUTES` | Duración del token en minutos (default: `60`) |
| `CREDENTIALS_SECRET` | Clave Fernet de 44 caracteres para cifrar mensajes y credenciales |
| `GEMINI_API_KEY` | API key de Gemini |
| `GROQ_API_KEY` | API key de Groq (fallback) |

Generar una clave Fernet válida:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Base de datos

Tablas creadas automáticamente al arrancar:

| Tabla | Descripción |
|---|---|
| `chats` | `chat_id`, `user_id`, `title`, `created_at`, `updated_at` |
| `chat_messages` | `id`, `chat_id`, `role`, `content` (cifrado), `created_at` |

---

## Ejecución local

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Primera vez:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

---

## Endpoints disponibles

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/health` | No | Estado del servidor |
| GET | `/auth/login` | No | Inicia el flujo OAuth con Google |
| GET | `/auth/callback` | No | Callback de Google. Devuelve JWT |
| GET | `/auth/me` | Bearer JWT | Devuelve identidad del usuario autenticado |
| GET | `/chat/list` | Bearer JWT | Lista todos los chats del usuario |
| POST | `/chat/new` | Bearer JWT | Crea un chat vacío |
| GET | `/chat/{id}/messages` | Bearer JWT | Devuelve el historial de mensajes de un chat |
| PATCH | `/chat/{id}/title` | Bearer JWT | Renombra un chat |
| DELETE | `/chat/{id}` | Bearer JWT | Elimina un chat y todos sus mensajes |
| POST | `/chat` | Bearer JWT | Genera una respuesta (no-streaming) |
| POST | `/chat/stream` | Bearer JWT | Genera una respuesta en streaming |
| GET | `/compiler/base` | Bearer JWT | Construye el BaseContext |
| GET | `/compiler/style` | Bearer JWT | Construye el StyleContext |
| GET | `/docs` | No | Documentación interactiva |

---

## Flujo de autenticación

```
Usuario → GET /auth/login → Google → GET /auth/callback → { token }
```

## Flujo de chat

```
POST /chat/stream  →  guarda mensaje usuario  →  stream respuesta  →  guarda respuesta  →  devuelve chat_id
```

---

## Arquitectura

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
└── core/
    ├── config.py
    ├── jwt.py
    ├── encryption.py
    └── database.py
```
