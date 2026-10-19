# Fase B — preparación de migración histórica

Estado: preparado en modo read-only. No aplicar SQL, no generar liquidaciones y no escribir en producción.

## 1. Identidad de propietarios

Fuentes revisadas:

- `magiis_crm_vehicle_owners`: tiene snapshot del proveedor y 122 registros, pero no es una fuente autorizada de Livre; no alcanza para atribuir identidad en el CRM propio.
- `livre_users`: es la fuente local de cuentas de Livre autorizada para contrastar identidad. La comparación por email y por `userId` del snapshot dio 0 coincidencias.
- `livre_driver_accounts`: vincula cuentas operativas de conductores, no acredita titularidad legal de un vehículo.
- `livre_driver_vehicles` y cierres de turnos: contienen etiquetas operativas, no una prueba autorizada de titularidad.

Resultado: no existe hoy una coincidencia autorizada de Livre que permita completar propietarios. El snapshot de MAGIIS puede conservarse como evidencia de origen, pero no se promociona automáticamente a identidad de Livre.

Información que necesitamos obtener de la organización responsable de Livre:

1. Identificador estable del propietario en Livre, o confirmación formal de que se adopta el ID de origen como referencia externa.
2. Nombre y apellido legales completos.
3. DNI/CUIT u otra identificación que Livre autorice a guardar.
4. Email y teléfono verificados.
5. Confirmación explícita de qué vehículos pertenecen a ese propietario, con el identificador estable de cada vehículo.
6. Fuente, responsable y fecha de verificación de cada dato.
7. Autorización de uso para CRM, liquidaciones y soporte, según corresponda.

La entrega debe venir de un responsable autorizado de Livre (dueño del padrón de propietarios o responsable operativo/financiero designado), no de una inferencia del snapshot ni de una búsqueda web. Hasta recibirla, `safe_candidates=0` y los 140 vehículos quedan fuera de la promoción operativa.

## 2. Revisión manual de pasajeros

El dry-run genera una cola no destructiva con:

- `source_magiis_id`.
- `reason`: `ambiguous_identity` o `missing_phone`.
- evidencia mínima para revisar.
- `status=pending_manual_review`.
- `decision=null`.

Los 49 ambiguos no se fusionan ni se descartan. La persona revisora debe elegir una de estas decisiones explícitas: `keep_separate`, `merge_into_existing`, `link_to_existing`, `reject_as_invalid` o `needs_more_evidence`. Ninguna decisión se aplica en producción en esta fase y `reject_as_invalid` requiere motivo y aprobación.

Política para los 23 sin teléfono:

- quedan en revisión manual y fuera de la migración automática;
- no se descartan, fusionan ni les inventa un teléfono;
- se puede completar sólo con una fuente autorizada y evidencia registrada;
- si no aparece un teléfono verificable, quedan como `needs_more_evidence` y no se habilitan para comunicaciones operativas.

## 3. Auditoría financiera

Para cada viaje con `gross_fare`, el audit compara:

`gross_fare = livre_commission + driver_net`

con redondeo monetario a 2 decimales, y verifica que `livre_commission = gross_fare * 15%` y `driver_net = gross_fare - commission`. También informa si la tasa/campos explícitos de la capa nueva están presentes.

`payment_confirmed=false` significa que el viaje no se considera pagado, aunque `financial_status=finalized`. `pending_settlement` tampoco se considera pagado. No se generan liquidaciones ni se marcan pagos.

El mecanismo no puede demostrar un pago externo; sólo puede confirmar consistencia del registro y bloquear el estado pagado hasta que exista una confirmación autorizada.

## 4. Diseño de vehículos y propietarios

Orden propuesto, cuando exista autorización:

1. Preparar propietarios con `source_magiis_id` y datos verificados.
2. Resolver manualmente conflictos y duplicados de propietarios.
3. Preparar vehículos con `source_magiis_id` y conservar `owner_source_magiis_id`.
4. Resolver el propietario local antes de llenar `owner_id`.
5. Insertar de forma aditiva, usando claves de origen únicas e idempotentes.
6. Verificar conteos, claves y relaciones antes de habilitar cualquier operación.

Un vehículo con propietario de origen conocido pero sin propietario Livre verificado queda `blocked_until_owner_resolution`; uno sin propietario queda igual bloqueado. Nunca se crea un propietario desde una placa, una etiqueta o un nombre operativo.

## Bloqueos de negocio

- Fuente autorizada y responsable para identidad de propietarios.
- Decisión sobre los 49 ambiguos.
- Fuente autorizada o resolución para los 23 sin teléfono.
- Confirmación de que la regla contable es 15% una sola vez y definición de qué evidencia habilita `paid`.
- Aprobación posterior para aplicar la migración aditiva y ejecutar cualquier liquidación.

## Verificación técnica

El script `scripts/historical_crm_dry_run.py` sólo hace `SELECT`, conecta con `default_transaction_read_only=on` y reporta la cola manual, auditoría financiera y plan de vehículos. Los tests de `tests/test_historical_migration.py` cubren no-fusión, no-descarte, cierre financiero, bloqueo de pagos pendientes y preservación de la relación por claves de origen.
