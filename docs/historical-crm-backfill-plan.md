# Plan de backfill histórico MAGIIS -> Livre CRM

Estado: preparado, no aplicado. El script `scripts/historical_crm_dry_run.py` abre la transacción en `default_transaction_read_only=on`; no inserta, actualiza ni borra.

## Regla de idempotencia

Cada entidad se identifica por `source_magiis_id`, que conserva el `magiis_id` histórico y tiene índice único parcial en las tablas locales. El backfill futuro debe usar `INSERT ... ON CONFLICT (source_magiis_id) DO UPDATE`, conservando `raw_payload` y sin crear filas duplicadas. No se debe usar nombre, teléfono o patente como identidad de origen.

## Mapeo exacto

### Clientes

Origen: `magiis_crm_passengers.magiis_id` + `raw_payload`.

- `source_magiis_id` <- `magiis_id`
- `first_name` <- `raw_payload.firstName`
- `last_name` <- `raw_payload.lastName`
- `email` <- `raw_payload.passengerUserEmail`
- `phone` <- `raw_payload.phoneNumber`
- `country` <- `raw_payload.country`, con default explícito `Argentina` sólo cuando está vacío
- `raw_payload` <- payload completo sin modificaciones

Inclusión: ID, nombre, apellido y teléfono no vacíos. Los registros con el mismo nombre/teléfono se mantienen separados por ID; no se fusionan.
Exclusión: 23 registros sin teléfono. Los 49 registros en 22 grupos ambiguos no se descartan por ambigüedad: se copian separados y quedan señalados para revisión.

### Propietarios

Origen: `magiis_crm_vehicle_owners.magiis_id` + `raw_payload`.

- `source_magiis_id` <- `magiis_id`
- `first_name` <- primer token de `raw_payload.titularDescription`
- `last_name` <- resto de `raw_payload.titularDescription`
- `identification` <- `raw_payload.identification`
- `email` <- `raw_payload.contactEmail`
- `phone` <- `raw_payload.contactPhone`
- `city` <- `raw_payload.city`
- `province` <- `raw_payload.province`
- `street` <- `raw_payload.streetName`
- `street_number` <- `raw_payload.streetNumber`
- `raw_payload` <- payload completo sin modificaciones

Inclusión: `titularDescription` con al menos dos tokens. No se consultan `firstName`/`lastName`, porque no existen en el snapshot de propietarios.
Exclusión: un propietario sin `titularDescription` utilizable. No se inventan nombres ni se vincula por email.

La separación del nombre es una normalización técnica mínima para satisfacer las columnas locales obligatorias; el nombre completo original siempre queda preservado en `raw_payload.titularDescription`.

### Vehículos

Origen: `magiis_crm_vehicles.magiis_id` + `raw_payload`.

- `source_magiis_id` <- `magiis_id`
- `plate` <- `raw_payload.identificationCard.domain`
- `brand` <- `raw_payload.identificationCard.mark`
- `model` <- `raw_payload.identificationCard.model`
- `year` <- `raw_payload.identificationCard.yearOfVehicle`
- `color` <- `raw_payload.identificationCard.color`
- `chassis` <- `raw_payload.identificationCard.chassis`
- `motor` <- `raw_payload.identificationCard.motor`
- `vehicle_type` <- `raw_payload.identificationCard.vehicleType` / `vehicleTypeId` / `type`
- `image_url` <- `raw_payload.photo`
- `raw_payload` <- payload completo sin modificaciones

Inclusión operativa: campos obligatorios completos y `ownerId` existente en `magiis_crm_vehicle_owners` elegible. `ownerId` no se copia directamente a `owner_id`: se resuelve `owner source_magiis_id -> livre_crm_vehicle_owners.id`.
Exclusión: campos obligatorios faltantes, propietario inexistente o propietario excluido por nombre faltante. Los vehículos sin propietario quedan en reporte y no se promueven todavía.

### Relaciones conductor-vehículo

Origen: `magiis_crm_drivers.raw_payload`.

- `driver_user_id` <- `driverUserId`, sólo si ya existe en `livre_users.id`
- `vehicle_magiis_id` <- `vehicleId`, sólo si existe en el snapshot histórico de vehículos
- `vehicle_label` <- `vehicleDomain` / `vehiclePlate`
- `is_primary` <- `FALSE` explícito; el histórico no aporta esa semántica

No se crea una identidad local de conductor automáticamente. La relación se inserta sólo cuando ambas identidades ya están autorizadas/locales. La clave primaria existente `(driver_user_id, vehicle_magiis_id)` evita duplicados.

### Viajes

No se incluye backfill de pasajeros en viajes. `magiis_crm_travels` no tiene un ID histórico confiable de pasajero; `passengerFirstName`/`passengerLastName` no se usan para vincular.

## Dry-run actual (sólo lectura)

Ejecutado contra la base actual:

| Entidad | Origen | Elegibles | Excluidos/bloqueados | Locales actuales | A insertar si se autoriza |
|---|---:|---:|---:|---:|---:|
| Clientes | 20.492 | 20.469 | 23 sin teléfono | 0 | 20.469 |
| Propietarios | 122 | 121 | 1 sin `titularDescription` | 0 | 121 |
| Vehículos operativos | 140 | 136 | 4 sin `ownerId`; ninguno con owner inexistente | 0 | 136 |
| Relaciones conductor-vehículo | 187 | 0 | 187 con conductor no local | 0 | 0 |

Detalles verificados:

- Los 121 propietarios elegibles tienen `titularDescription`; 121 tienen nombre separable.
- Los 136 vehículos con `ownerId` encuentran propietario histórico existente.
- Hay 4 vehículos sin `ownerId`.
- No se detectaron `ownerId` apuntando a propietarios históricos inexistentes.
- Hay 182 filas históricas de conductor con `vehicleId`, pero ninguna puede insertarse sin que `driverUserId` exista primero en `livre_users`.
- Las tablas locales están en cero y todavía no tienen `source_magiis_id`; por eso el dry-run marca la migración de claves como prerrequisito.

## Orden futuro, todavía no autorizado

1. Aplicar sólo `app/migrations/20261019_historical_crm_source_keys.sql`.
2. Backfill de propietarios y clientes por `source_magiis_id`.
3. Resolver y backfillear vehículos usando el mapa de propietarios local.
4. Insertar relaciones conductor-vehículo sólo para conductores ya locales.
5. Repetir el dry-run y verificar conteos, unicidad de source IDs, FKs y preservación de payload.

Este plan no ejecutó ninguna escritura productiva.
