# Auditoría de cotizaciones históricas MAGIIS

Fecha de consulta: 2026-09-25
Fuente: PostgreSQL de Railway, entorno production
Modo: solo lectura

## Resultado

Se encontraron:

- 24 solicitudes históricas en `magiis_quote_requests`.
- 96 opciones de cotización en `magiis_quotes`.
- Período: 2026-09-10 a 2026-09-14.
- Todas las cotizaciones tienen distancia, duración, `rate_id`, `price_point_to_point` y `total_cost_price`.
- En los registros auditados, `price_point_to_point` y `total_cost_price` coinciden.

## Tarifas encontradas

| rate_id | nombre | pricePerKm observados |
|---:|---|---|
| 6243 | Base | 1046.5, 1449, 1610 |
| 9786 | Tarifa Nocturna | 1063.92, 1473.12, 1636.8 |

También aparecen los tipos de transporte `1`, `2`, `3` y `81`, y los tipos de vehículo `1` y `2`.

## Componentes guardados

Cada `quote_body` conserva, además del precio final:

- `distance` y `routeDistance`;
- `pricePerKm`;
- `kmIncluded`;
- `rateId` y `rateName`;
- `taxesCost`;
- `waitTotalPrice`;
- `otherCosts`;
- `fixConceptsCost` y `fixConceptList`;
- `taxiingCost`;
- `tipAmount` y `tipPercentage`;
- `totalCostPrice` y `pricePointToPoint`.

En las 96 filas auditadas los valores agregados relevantes fueron:

- impuestos: `0`;
- espera: `0`;
- taxiing: `0`;
- conceptos fijos: `0`;
- propinas: sin importe registrado;
- otros costos: sin importe registrado.

## Reconstrucción

La evidencia permite describir el cálculo histórico como una combinación de:

```text
precio final = componentes de tarifa MAGIIS
             + distancia efectiva
             + pricePerKm según tarifa/categoría
             + ajustes internos de MAGIIS
```

La hipótesis simple `base fija + distancia redondeada * pricePerKm` reproduce prácticamente todos los valores, pero no todos exactamente: aparecen diferencias de hasta 1 unidad monetaria. La causa probable es que la tabla guarda `distance` con dos decimales, mientras que MAGIIS calculó con una distancia de mayor precisión antes de redondear el precio.

Por eso no se debe implementar todavía una fórmula propia usando únicamente la distancia almacenada. Los registros sí permiten reconstruir el comportamiento, pero para clavar cada resultado hay que recuperar la distancia efectiva o la configuración histórica completa de cada tarifa.

## Conclusión

Las cuentas anteriores sí se pueden rescatar. No están perdidas: los componentes necesarios quedaron dentro de `quote_body`. La fórmula no estaba escrita en Livre, sino aplicada por MAGIIS.

El siguiente paso seguro es construir un extractor que genere una tabla normalizada por cotización y, si se dispone de más precisión en `routeDistance` o de la configuración de los `rate_id`, validar la fórmula exacta. No se modificó la lógica de producción ni se hicieron llamadas de escritura a MAGIIS.
