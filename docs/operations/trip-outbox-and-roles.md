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
2. Aplicar `20261022_trip_operations_audit.sql` y luego `20261023_user_roles_outbox_retention.sql` en una base aislada.
3. Ejecutar la suite y comprobar que el worker puede reclamar, fallar y reintentar sin duplicar eventos.
4. Configurar el worker separado y revisar logs antes de habilitar notificaciones reales.

Migraciones y operación:

- Para producción, configurar `AUTO_MIGRATE=false` en el servicio web y ejecutar antes `python scripts/run_migrations.py` como job controlado.
- El runner toma un advisory lock, guarda SHA-256 por migración y aborta si una migración aplicada fue modificada. Instalaciones anteriores reciben un backfill único de checksum y dejan warning en logs.
- `GET /crm/operational/metrics` requiere autenticación CRM y expone viajes por estado, outbox pendiente/fallido/enviado y viajes con GPS atrasado.
- `GET/PUT /crm/users/{user_id}/roles` permite a administradores CRM administrar únicamente `crm` y `crm_admin`.
- Los límites de login, solicitudes, estados y GPS son por instancia. Con varias réplicas deben duplicarse en el proxy o migrarse a un contador compartido.
- Los endpoints de webhook no usan este módulo y no fueron modificados.
