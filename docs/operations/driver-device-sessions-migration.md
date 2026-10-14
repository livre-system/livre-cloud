# Migración de sesiones de dispositivo del conductor

Estado: preparada, no ejecutada.

Alcance: producción Railway, servicio `Postgres`, base `railway`.

No ejecutar este procedimiento sin autorización explícita para escribir en PostgreSQL.

## Evidencia del esquema actual

Consulta realizada contra `DATABASE_PUBLIC_URL` con:

- `default_transaction_read_only=on`
- `statement_timeout=10000`
- sin `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER` ni `DROP`

Resultado:

- `livre_driver_device_sessions`: no existe.
- `schema_migrations`: contiene `20261012_driver_device_sessions.sql`, no contiene `20261014_driver_device_sessions.sql`.
- `livre_driver_accounts`: conserva `active_device_id`, `device_session_updated_at` y los índices de la implementación de un solo dispositivo.
- 195 cuentas activas.
- 10 cuentas activas tienen `active_device_id` no vacío y serán candidatas al backfill.
- No hay IDs de dispositivo duplicados entre esas 10 cuentas.
- No hay cuentas huérfanas respecto de `livre_users`.
- 185 cuentas activas no tienen dispositivo asociado; no se inventan sesiones para ellas.

La migración `20261014_driver_device_sessions.sql` es compatible con el esquema real: referencia `livre_users(id)`, copia los 10 dispositivos existentes con `ON CONFLICT`, crea la clave primaria `(user_id, device_id)` y agrega el índice parcial de sesiones activas.

## Orden de ejecución autorizado

### 0. Preflight, sin escritura

Verificar desde el checkout correcto:

```bash
cd /home/calambre/Documentos/Clientes/Livre/livre-cloud
git status --short --branch
railway status
railway variable list --service Postgres --environment production --json
```

Confirmar nuevamente, en una conexión de solo lectura, que:

```sql
SELECT to_regclass('public.livre_driver_device_sessions');
SELECT version, applied_at
FROM public.schema_migrations
WHERE version = '20261014_driver_device_sessions.sql';
```

Ambas consultas deben devolver `NULL` antes de aplicar.

### 1. Aplicar solo la migración, dentro de una transacción

Usar la URL pública de Railway sin imprimirla ni guardarla en archivos compartidos. El archivo SQL se ejecuta desde el checkout local:

La migración y el registro del ledger deben ocurrir en la misma transacción, en una única sesión `psql`:

```bash
cd /home/calambre/Documentos/Clientes/Livre/livre-cloud
export DATABASE_PUBLIC_URL="$(railway variable list --service Postgres --environment production --json | python -c 'import json,sys; print(json.load(sys.stdin)["DATABASE_PUBLIC_URL"])')"

psql "$DATABASE_PUBLIC_URL" \
  --set=ON_ERROR_STOP=1 \
  <<'SQL'
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SELECT pg_advisory_xact_lock(123456789);
\i app/migrations/20261014_driver_device_sessions.sql
INSERT INTO public.schema_migrations (version)
VALUES ('20261014_driver_device_sessions.sql');
COMMIT;
SQL
```

Si `lock_timeout`, la migración o el `INSERT` fallan, la transacción debe quedar revertida y no se debe avanzar al deploy. No usar `ON CONFLICT` en el registro del ledger: si ya existe, detenerse e investigar.

### 2. Validación inmediata, aún sin deploy

Ejecutar en una conexión de solo lectura:

```sql
SELECT to_regclass('public.livre_driver_device_sessions');

SELECT version
FROM public.schema_migrations
WHERE version = '20261014_driver_device_sessions.sql';

SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'public.livre_driver_device_sessions'::regclass
ORDER BY conname;

SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'livre_driver_device_sessions'
ORDER BY indexname;

SELECT count(*) AS sessions,
       count(*) FILTER (WHERE active) AS active_sessions,
       count(DISTINCT user_id) AS users_with_sessions
FROM public.livre_driver_device_sessions;

SELECT a.user_id, a.active_device_id
FROM public.livre_driver_accounts a
WHERE a.active = TRUE
  AND a.active_device_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1
    FROM public.livre_driver_device_sessions s
    WHERE s.user_id = a.user_id
      AND s.device_id = a.active_device_id
  );
```

Resultados esperados con el estado observado: tabla existente, ledger con una fila, PK `(user_id, device_id)`, índice parcial `idx_livre_driver_device_sessions_active`, 10 sesiones y la última consulta sin filas.

### 3. Deploy del backend

Solo después de validar la base:

1. Revisar el diff local y autorizar el cambio de código por separado.
2. Hacer commit/push/deploy del backend en una operación posterior.
3. No desplegar el backend antes del paso 1: `lifespan()` ejecuta `run_migrations()` y `_assert_driver_device_sessions_schema()` durante el arranque; con la tabla ausente el nuevo backend falla deliberadamente.
4. El nuevo deploy verá el registro del ledger, omitirá la migración y pasará la aserción sin modificar la base.

## Continuidad del servicio

La migración es aditiva y breve: crea una tabla nueva, copia como máximo las 10 filas observadas y crea un índice sobre esa tabla. No altera ni bloquea por escritura `livre_driver_accounts`, `livre_users` ni `livi_trips`.

La transición sin incompatibilidad es:

1. backend actual en servicio;
2. migración + backfill + ledger en una sola transacción;
3. validación de solo lectura;
4. deploy del backend nuevo.

No aplicar migración desde el arranque del backend nuevo en esta ocasión: Railway tiene una sola réplica configurada y no tiene `healthcheckPath`/`overlapSeconds` definidos en el manifiesto observado; hacerlo durante el boot aumenta el riesgo de una ventana sin servicio.

Antes y después del deploy comprobar:

```bash
curl -fsS https://livre-cloud-production.up.railway.app/health
railway service status --service livre-cloud --environment production
```

Luego ejecutar una prueba operativa controlada con una cuenta autorizada: login de conductor, `POST /mobility/driver/location` con `device_id`, y logout. Verificar solo las respuestas y, después, leer la fila de esa sesión. No usar credenciales reales en logs ni reportes.

## Compatibilidad con la APK actual

No se modifica la APK. El código nativo actual:

- envía `device_id` en el JSON del login;
- envía `latitude`, `longitude`, `accuracy` y `device_id` en cada actualización GPS;
- conserva el mismo endpoint `/mobility/driver/location`;
- usa el mismo endpoint de viaje cuando corresponde;
- envía `X-Device-ID` al cerrar sesión.

El backend nuevo conserva esos contratos. Los 10 `active_device_id` existentes se migran, por lo que los tokens/dispositivos actuales permanecen válidos. Las cuentas sin dispositivo deberán iniciar sesión nuevamente para crear su sesión. No se cambia el formato del JWT ni el contrato de la APK.

## Rollback

### Antes del deploy del backend

Si la migración falla, `psql` debe hacer rollback automático por la transacción. Verificar que la tabla y el ledger sigan ausentes.

Si la migración terminó correctamente pero se decide no continuar antes del deploy, ejecutar solo con autorización explícita:

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';
SELECT pg_advisory_xact_lock(123456789);
DROP TABLE IF EXISTS public.livre_driver_device_sessions;
DELETE FROM public.schema_migrations
WHERE version = '20261014_driver_device_sessions.sql';
COMMIT;
```

### Después del deploy del backend

No borrar la tabla mientras el backend nuevo esté activo. Primero volver al artefacto anterior y esperar su estado `SUCCESS`; el código anterior solo usa las columnas legacy. Luego, si se requiere retirar la migración, ejecutar el rollback anterior en una ventana controlada y verificar que la tabla esté ausente antes de volver a desplegar el código nuevo.

Si el problema es funcional pero la tabla está sana, el rollback preferido es de aplicación: volver al backend anterior y conservar la tabla. Esto evita perder sesiones ya creadas y permite investigar sin una segunda escritura destructiva.

## Bloqueos actuales

- No se aplicó la migración.
- No se escribió en PostgreSQL.
- No se creó staging ni otra base.
- No se modificó la APK.
- No se hizo commit, push ni deploy.
