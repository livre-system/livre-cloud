"""Lectura de datos operativos de MAGIIS Web para el futuro CRM.

Este módulo es deliberadamente independiente de los flujos de cotización,
creación, auditoría y cancelación de ``magiis_web``. Solo realiza requests GET
observadas en la captura HAR y no persiste ni modifica datos en MAGIIS.
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from app.integrations import magiis_web


CRM_BASE_URL = magiis_web.BASE_URL
CRM_TIMEOUT = magiis_web.TIMEOUT


class MagiisCrmReader:
    """Cliente de consultas GET para relevamiento/sincronización futura."""

    def __init__(
        self,
        session: Mapping[str, Any],
        *,
        carrier_id: int = magiis_web.CARRIER_USER_ID,
        client: httpx.Client | None = None,
    ) -> None:
        self.session = dict(session)
        self.carrier_id = carrier_id
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=CRM_BASE_URL,
            timeout=CRM_TIMEOUT,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "MagiisCrmReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        """Ejecuta un GET autenticado; ante 401/403 reloguea una sola vez."""
        for attempt in range(2):
            response = self.client.get(
                path,
                params=dict(params or {}),
                headers={"Authorization": f"Bearer {self.session['token']}"},
            )
            if response.status_code not in (401, 403) or attempt == 1:
                response.raise_for_status()
                return response.json()
            self.session = magiis_web.login(self.client)
        raise AssertionError("unreachable")

    def travel_page(
        self,
        *,
        page: int = 1,
        size: int = 20,
        travel_date_from: str,
        travel_date_to: str,
        state: str = "",
        programmed: str = "",
        find: str = "",
        is_pax_vip: bool = False,
        sort: str = "ASC",
    ) -> dict[str, Any]:
        """Obtiene una página de viajes del carrier, sin crear ni alterar viajes."""
        return self._get(
            f"/carriers/{self.carrier_id}/travels/paginated",
            params={
                "page": page,
                "size": size,
                "column": "travelDate",
                "sort": sort,
                "state": state,
                "platform": "false",
                "find": find,
                "programmed": programmed,
                "travelDateFrom": travel_date_from,
                "travelDateTo": travel_date_to,
                "isPaxVip": str(is_pax_vip).lower(),
            },
        )

    def travel_totals(
        self,
        *,
        travel_date_from: str,
        travel_date_to: str,
        find: str = "",
        programmed: str = "",
        is_pax_vip: bool = False,
    ) -> dict[str, Any]:
        return self._get(
            f"/carriers/{self.carrier_id}/travels/totals",
            params={
                "find": find,
                "programmed": programmed,
                "travelDateFrom": travel_date_from,
                "travelDateTo": travel_date_to,
                "isPaxVip": str(is_pax_vip).lower(),
            },
        )

    def passenger_page(
        self, *, page: int = 1, size: int = 20, find: str = "", sort: str = "ASC"
    ) -> dict[str, Any]:
        return self._get(
            f"/carriers/{self.carrier_id}/clients/paginated",
            params={
                "page": page,
                "size": size,
                "column": "code",
                "sort": sort,
                "find": find,
                "isPaxVip": "false",
            },
        )

    def passenger(self, passenger_id: int) -> dict[str, Any]:
        return self._get(f"/carriers/{self.carrier_id}/clients/{passenger_id}")

    def driver_dashboard(self, *, page: int = 1, size: int = 999999) -> dict[str, Any]:
        return self._get(
            f"/carriers/{self.carrier_id}/drivers/dashboard",
            params={
                "page": page,
                "size": size,
                "column": "currentBase",
                "sort": "ASC",
                "value": "",
                "states": "",
                "driverId": "",
                "vehicleIdSwitch": "false",
            },
        )

    def driver(self, driver_user_id: int) -> dict[str, Any]:
        return self._get(f"/drivers/{driver_user_id}")

    def vehicle_page(
        self, *, page: int = 1, size: int = 20, find: str = "", sort: str = "ASC"
    ) -> dict[str, Any]:
        return self._get(
            f"/carriers/{self.carrier_id}/vehicles/paginated",
            params={
                "page": page,
                "size": size,
                "column": "id",
                "sort": sort,
                "find": find,
            },
        )

    def vehicle(self, vehicle_id: int) -> dict[str, Any]:
        return self._get(f"/vehicles/{vehicle_id}")

    def vehicle_types(self) -> list[dict[str, Any]]:
        return self._get(
            f"/vehicles/{self.carrier_id}/types",
            params={"carrierHasCars": "true"},
        )

    def operational_status(self) -> dict[str, Any]:
        return self._get(f"/carriers/travelsCurrentStatus/carrier/{self.carrier_id}")

    def client_totals(self) -> dict[str, Any]:
        return self._get(f"/carriers/{self.carrier_id}/clients/totalClients")

    def driver_totals(self) -> dict[str, Any]:
        return self._get(f"/carriers/{self.carrier_id}/drivers/totalDrivers")

    def drivers_for_combo(self) -> list[dict[str, Any]]:
        return self._get(f"/carriers/{self.carrier_id}/drivers/driversForCombo")

    def vehicles(self) -> list[dict[str, Any]]:
        return self._get(f"/carriers/{self.carrier_id}/vehicles")

    def vehicle_drivers(self, vehicle_id: int) -> list[dict[str, Any]]:
        return self._get(
            f"/carriers/{self.carrier_id}/vehicleOwners/vehicle/{vehicle_id}/drivers"
        )

    def contractors_page(
        self, *, page: int = 1, size: int = 20, sort: str = "ASC"
    ) -> dict[str, Any]:
        return self._get(
            f"/carrier/{self.carrier_id}/contractors/paginated",
            params={
                "page": page,
                "size": size,
                "column": "id",
                "sort": sort,
            },
        )

    def passenger_checking_account_page(
        self, passenger_id: int, *, page: int = 1, size: int = 10
    ) -> dict[str, Any]:
        return self._get(
            f"/passengers/{passenger_id}/travelsCheckingAccount/paginated",
            params={
                "page": page,
                "size": size,
                "column": "travelDate",
                "sort": "DESC",
            },
        )

    @staticmethod
    def _page_content(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int, bool]:
        """Extrae el contenido de las dos envolturas de paginación observadas."""
        page = payload.get("page", payload)
        content = page.get("content", []) if isinstance(page, Mapping) else []
        total_pages = page.get("totalPages", 1) if isinstance(page, Mapping) else 1
        last = page.get("last", True) if isinstance(page, Mapping) else True
        return list(content), int(total_pages), bool(last)

    def iter_travels(
        self,
        *,
        travel_date_from: str,
        travel_date_to: str,
        state: str = "",
        size: int = 100,
    ) -> Any:
        """Itera viajes sin cargar todo el histórico en memoria."""
        page = 1
        while True:
            payload = self.travel_page(
                page=page,
                size=size,
                travel_date_from=travel_date_from,
                travel_date_to=travel_date_to,
                state=state,
            )
            content, total_pages, last = self._page_content(payload)
            yield from content
            if last or page >= total_pages:
                break
            page += 1

    def iter_passengers(self, *, size: int = 100) -> Any:
        """Itera todos los pasajeros visibles para el carrier."""
        page = 1
        while True:
            payload = self.passenger_page(page=page, size=size)
            content, total_pages, last = self._page_content(payload)
            yield from content
            if last or page >= total_pages:
                break
            page += 1

    def iter_drivers(self, *, size: int = 100) -> Any:
        """Itera el dashboard, que el Web devuelve como lista."""
        payload = self.driver_dashboard(size=999999)
        if isinstance(payload, list):
            yield from payload
            return
        content, _, _ = self._page_content(payload)
        yield from content

    def iter_vehicles(self, *, size: int = 100) -> Any:
        """Itera todos los vehículos visibles para el carrier."""
        page = 1
        while True:
            payload = self.vehicle_page(page=page, size=size)
            content, total_pages, last = self._page_content(payload)
            yield from content
            if last or page >= total_pages:
                break
            page += 1

    def service_rate(self) -> dict[str, Any]:
        return self._get(f"/carriers/rate/default/{self.carrier_id}")


__all__ = ["MagiisCrmReader", "CRM_BASE_URL", "CRM_TIMEOUT"]
