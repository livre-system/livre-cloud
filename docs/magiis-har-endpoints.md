# Inventario MAGIIS extraído del HAR

Fuente: `1apps.magiis.com.har`.

El HAR contiene 800 entradas. Se detectaron 3 hosts operativos de MAGIIS:

- `https://api.apps.magiis.com`: API Web/CRM.
- `https://svc.magiis.com`: chats MMC.
- `https://apps.magiis.com`: frontend y recursos estáticos.

No se copian tokens, cookies, contraseñas, emails ni respuestas completas.
Las peticiones `OPTIONS` son preflight CORS y no son operaciones de negocio.

## Autenticación

```text
POST https://api.apps.magiis.com/auth/login
Body: { username, password }
Respuesta relevante: token, userId, roleUser, userPrivileges, enabledUser
Header posterior: Authorization: Bearer <token>
```

El HAR contiene credenciales y tokens sensibles. No deben subir al repositorio.
Para Livre Cloud usar las variables de Railway y pedir un Bearer nuevo por API.

## Gestión de viajes

```text
GET /carriers/{carrierId}/travels/paginated
Query:
  page, size, column, sort,
  state, platform, find, programmed,
  travelDateFrom, travelDateTo, isPaxVip
Respuesta: { page: { content, totalPages, totalElements, ... } }
```

En la captura se usó:

```text
page=1
size=20
column=travelDate
sort=ASC
state=SEARCHING_DRIVER-WITH_DRIVER_ASSIGNED-RESERVED
platform=false
find=
programmed=
travelDateFrom=YYYY-MM-DD
travelDateTo=YYYY-MM-DD
isPaxVip=false
```

```text
GET /carriers/{carrierId}/travels/totals
Query: find, isPaxVip, programmed, travelDateFrom, travelDateTo
Respuesta: toAsignQuantity, scheduledQuantity, inProgressQuantity,
           lostQuantity, doneQuantity, cancelledQuantity, inConflictQuantity
```

## Choferes

```text
GET /carriers/{carrierId}/drivers/dashboard
Query: page, size, column, sort, value, states,
       driverId, vehicleIdSwitch
Respuesta por fila: driverUserId, driverIdForCarrier, firstName, lastName,
  vehicle, driverState, onSchedule, currentBase, queueFifoPosition,
  vehicles, branchId, totalDriversInBase, lastPlace, lastSee, schedule
```

```text
GET /carriers/{carrierId}/drivers/driversForCombo
Respuesta: driverUserId, firstName, lastName
```

```text
GET /carriers/{carrierId}/drivers/totalDrivers
Query: driverId
Respuesta: totalDrivers, totalDriversOnline, totalDriversOffline,
           totalDriversInTravel, totalDriversOnScheduleOffline
```

```text
GET /drivers/{driverId}
Respuesta detallada: identidad, contacto, estado, rating, vehículos,
  documentos, ubicación, agenda y métricas
```

```text
GET /carriers/{carrierId}/drivers/{driverId}/checkingAccountMovements/paginated
```

## Vehículos

```text
GET /carriers/{carrierId}/vehicles/paginated
Query: page, size, column, sort, find
Respuesta: { content, totalPages, totalElements, ... }
```

```text
GET /carriers/{carrierId}/vehicles
Query: domain
Respuesta: vehicleId, vehicleIdForCarrier, enabled, identificationCard,
  characteristics, vehicleOwner, commissionSettings, photo, documents
```

```text
GET /vehicles/{vehicleId}
Respuesta detallada: identificationCard, characteristics, owner,
  commissionSettings, documents, additionalTypes
```

```text
GET /vehicles/{carrierId}/types
Query: carrierHasCars
Respuesta: id, name, description, maxPassengers, maxLuggage,
  onDemandPax, programmedPax, active
```

```text
GET /carriers/{carrierId}/vehicleOwners/unlinkedToDriver
GET /carriers/{carrierId}/vehicleOwners/vehicle/{vehicleId}/drivers
```

## Clientes / pasajeros

```text
GET /carriers/{carrierId}/clients/paginated
Query: page, size, column, sort, find, isPaxVip
Respuesta: { content, totalPages, totalElements, ... }
```

```text
GET /carriers/{carrierId}/clients/totalClients
Respuesta: countApp, countConcierge, countLocal, countOperator,
           countPassenger, countTripSpecialist, countWeb
```

```text
GET /carriers/{carrierId}/clients/{clientId}
GET /passengers/{passengerId}
GET /passengers/{passengerId}/driverPreference/carrier/{carrierId}
GET /passengers/{passengerId}/travelsCheckingAccount/paginated
GET /carriers/{carrierId}/passenger/{passengerId}/hasCheckingAccount
GET /carriers/{carrierId}/passenger/{passengerId}/checkingAccount/payments/paginated
```

## Otros endpoints CRM observados

```text
GET  /carriers/{carrierId}/branches
GET  /carriers/byUser/{userId}
GET  /carriers/rate/default/{carrierId}
GET  /carriers/{carrierId}/parameters
GET  /carriers/{carrierId}/parameter
GET  /carriers/{carrierId}/shortcuts
GET  /carriers/{carrierId}/termsAndConditionsFlag
GET  /carriers/travelsCurrentStatus/carrier/{carrierId}
GET  /checking-accounts/carrier/{carrierId}/movements
GET  /config/serverInfo
GET  /data/documents/DRIVER
GET  /data/documents/VEHICLE
GET  /multiregion/countries
GET  /multiregion/country/{country}
GET  /multiregion/states/countryId/{countryId}
POST /carriers/{carrierId}/otherCosts/search
PUT  /carriers/{carrierId}/carrierUser/{userId}/updatePBXCode
POST /users/updateFirebaseToken
POST /usertrail/register
```

`POST /usertrail/register` usa las claves `carrierId`, `actionCode`,
`actionType`, `businessData`, `userId`. Es auditoría de la aplicación, no
necesaria para listar datos en Livre Cloud.

## Reportes genéricos observados

```text
POST /data/genericReport/{reportCode}/paginated
Body: { page, column, sort, size, reportCode, filters }
Respuesta: { content, totalPages, totalElements, ... }
```

Códigos observados: `018`, `019`, `042`, `079`, `082`, `108D`, `114`.
Los filtros observados usan principalmente `carrierId`, y según reporte
`driverCode`, `vehicleId` o `CARRIERID`.

## Servicio de chats

```text
GET /mmc/chats/all
Respuesta paginada: { content, totalPages, totalElements, ... }
```

Host: `https://svc.magiis.com`.

## Recursos no funcionales

`apps.magiis.com` también carga JavaScript, fuentes, imágenes, mapas,
OpenStreetMap, Google Maps, Cloudinary y Mercado Pago. No son endpoints CRM
para alimentar las vistas de Viajes, Choferes o Vehículos.

## Siguiente integración recomendada

1. Mantener `api.apps.magiis.com` únicamente detrás del backend.
2. Renovar token con las credenciales de Railway; nunca desde el HAR.
3. Usar `/travels/paginated` + `/travels/totals` para Gestión de Viajes.
4. Usar `/drivers/dashboard` + `/drivers/totalDrivers` para Choferes.
5. Usar `/vehicles/paginated` + `/vehicles/types` para Vehículos.
6. Adaptar el frontend a los contratos reales sin inventar campos.
7. No ejecutar operaciones de escritura hasta autorizar cada endpoint.
