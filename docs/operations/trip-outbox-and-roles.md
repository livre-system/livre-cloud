# Operación del outbox de viajes

El backend persiste auditoría y eventos en la misma transacción que el cambio de viaje.
El worker opcional `python -m app.core.outbox` entrega esos eventos a `OUTBOX_WEBHOOK_URL`.

Configuración segura:

- No activar `OUTBOX_WEBHOOK_URL` hasta tener un consumidor autenticado y preparado para `event_key` idempotente.
- El worker reintenta con backoff, recupera eventos atascados en `processing` después de cinco minutos y no marca `sent` si el sink no responde 2xx.
- Ejecutar el worker como proceso separado de la web; no iniciarlo dentro del lifespan de FastAPI.
- Revisar periódicamente eventos `failed` y enviados antiguos. La retención de outbox enviado debe definirse según auditoría/legal antes de automatizar borrado.

Roles CRM:

Insertar explícitamente los roles autorizados en `livre_user_roles` con una migración o procedimiento administrativo controlado. No guardar roles en variables del frontend ni aceptar roles enviados por el cliente.

Validación previa a Railway:

1. Backup verificable de PostgreSQL.
2. Aplicar las migraciones ordenadas en una base aislada, incluyendo `20261024_migration_checksums.sql`.
3. Ejecutar la suite y comprobar que el worker puede reclamar, fallar y reintentar sin duplicar eventos.
4. Configurar el worker separado y revisar logs antes de habilitar notificaciones reales.

Migraciones y operación:

- Para producción, configurar `AUTO_MIGRATE=false` en el servicio web y ejecutar antes `python scripts/run_migrations.py` como job controlado.
- `GET /health` solo confirma que el proceso está vivo. `GET /ready` confirma conexión a PostgreSQL y existencia de `schema_migrations`; devuelve 503 si la app no está lista para tráfico.
- `JWT_SECRET`, `SESSION_SECRET` y `DATABASE_URL` son obligatorios en `production`, `prod` y `staging`; el arranque falla temprano si falta alguno.
- El runner toma un advisory lock, guarda SHA-256 por migración y aborta si una migración aplicada fue modificada. El backfill de instalaciones anteriores requiere `ALLOW_LEGACY_MIGRATION_CHECKSUM_BACKFILL=true` durante esa operación explícita y controlada.
- Los cambios de roles quedan registrados en `livre_user_role_audit` con operador, usuario afectado y roles anteriores/nuevos.
- `GET /crm/operational/metrics` requiere autenticación CRM y expone viajes por estado, outbox pendiente/fallido/enviado y viajes con GPS atrasado.
- `GET/PUT /crm/users/{user_id}/roles` permite a administradores CRM administrar únicamente `crm` y `crm_admin`.
- Los límites de login, solicitudes, estados y GPS son por instancia. Con varias réplicas deben duplicarse en el proxy o migrarse a un contador compartido.
- Los endpoints de webhook no usan este módulo y no fueron modificados.
