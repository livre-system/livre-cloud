# HASH Cloud — Mapa Conceptual

```
hash-cloud/
│
├── app/
│   ├── main.py             Aplicación FastAPI. Registro de middleware y routers.
│   │
│   ├── auth/
│   │   └── router.py       Endpoints OAuth y JWT (/login, /callback, /me).
│   │
│   ├── context/
│   │   ├── provider.py     ContextProvider. Expone el contexto propio de HASH.
│   │   └── router.py       Endpoint /context. Devuelve identidad del usuario y contexto de HASH.
│   │
│   ├── memory/
│   │   ├── repository.py       Asociación user_id → spreadsheet_id. Único punto de cambio al migrar persistencia.
│   │   ├── service.py          Lógica de negocio: verificar, crear y asociar memoria del usuario.
│   │   ├── router.py           Endpoints /memory/status, /memory/session, /memory/authorize, /memory/callback.
│   │   └── memory_index.json   Índice local: user_id → spreadsheet_id. No versionado.
│   │
│   ├── api/                Punto de entrada de las solicitudes HTTP externas.
│   ├── cognition/          Orquestación del proceso de razonamiento y respuesta.
│   ├── compiler/           Compila memoria de HASH y memoria del usuario como entradas independientes
│   │                       para construir el contexto temporal del motor de respuesta.
│   ├── core/
│   │   ├── config.py                   Lectura de variables de entorno.
│   │   ├── jwt.py                      Generación, validación y dependencia de autenticación.
│   │   ├── encryption.py               Cifrado y descifrado de credenciales con clave del servidor.
│   │   ├── credentials_repository.py   Asociación user_id → refresh_token cifrado. No versionado.
│   │   └── credentials.json            Credenciales cifradas de Google por usuario. No versionado.
│   │
│   └── services/           Integración con servicios externos.
│
├── docs/               Documentación técnica del proyecto.
├── tests/              Pruebas del sistema.
│
├── .dockerignore       Archivos excluidos de la imagen Docker.
├── .env                Variables de entorno (no versionado).
├── .gitignore          Archivos excluidos del control de versiones.
├── Dockerfile          Imagen Docker para ejecutar HASH Cloud en cualquier entorno.
├── LICENSE             Licencia del proyecto.
├── README.md           Documentación viva del proyecto.
├── requirements.txt    Dependencias del proyecto.
└── tree.md             Este archivo. Mapa conceptual de la arquitectura.
```

---

**Sprint 1.1** — Estructura base inicializada.
**Sprint 1.2** — `app/main.py` agregado. FastAPI operativo con `/health`.
**Sprint 1.3** — `Dockerfile` y `.dockerignore` agregados. `.env` completado.
**Sprint 2.1** — `app/auth/router.py` y `app/core/config.py` agregados. Google OAuth operativo.
**Sprint 2.2** — `app/core/jwt.py` agregado. JWT generado en callback y validado en `/auth/me`.
**Sprint 2.3** — `require_auth` implementado como dependencia. Validación centralizada en `jwt.py`.
**Sprint 3.1** — `app/context/router.py` agregado. `/context` devuelve identidad del usuario autenticado.
**Sprint 3.2** — `app/context/provider.py` agregado. ContextProvider expone las cuatro fuentes del sistema.
**Sprint 4.1** — `app/memory/` operativo. Verificación, autorización incremental y creación de memoria del usuario en Google Sheets. Refresh token cifrado persistido en `app/core/credentials.json`.
