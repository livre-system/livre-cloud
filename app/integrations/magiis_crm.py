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
CRM_MMC_BASE_URL = "https://svc.magiis.com"


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
        self.mmc_client = httpx.Client(
            base_url=CRM_MMC_BASE_URL,
            timeout=CRM_TIMEOUT,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
        self.mmc_client.close()

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

    def _mutate(self, method: str, path: str, *, payload: Mapping[str, Any]) -> Any:
        """Ejecuta una mutación Web autenticada y renueva el token una vez."""
        for attempt in range(2):
            response = self.client.request(
                method,
                path,
                json=dict(payload),
                headers={"Authorization": f"Bearer {self.session['token']}"},
            )
            if response.status_code not in (401, 403) or attempt == 1:
                response.raise_for_status()
                return response.json() if response.content else {"status": "ok"}
            self.session = magiis_web.login(self.client)
        raise AssertionError("unreachable")

    def confirm_email(self, *, email: str, role: str) -> Any:
        return self._mutate("POST", "/users/searchEmailExists/", payload={
            "email": email,
            "role": role,
            "carrierId": self.carrier_id,
        })

    def _post_report(
        self,
        report_code: str,
        *,
        filters: list[dict[str, Any]],
        page: int = 1,
        size: int = 1000000,
        column: str = "",
        sort: str = "ASC",
    ) -> dict[str, Any]:
        payload = {
            "page": page,
            "column": column,
            "sort": sort,
            "size": size,
            "reportCode": report_code,
            "filters": filters,
        }
        params = {"page": page, "size": size, "column": column, "sort": sort}
        if report_code == "023":
            params["isOrderer"] = "true"
            payload["isOrderer"] = True
        for attempt in range(2):
            response = self.client.post(
                f"/data/genericReport/{report_code}/paginated",
                params=params,
                json=payload,
                headers={"Authorization": f"Bearer {self.session['token']}"},
            )
            if response.status_code not in (401, 403) or attempt == 1:
                response.raise_for_status()
                return response.json()
            self.session = magiis_web.login(self.client)
        raise AssertionError("unreachable")

    def driver_report_page(
        self,
        *,
        page: int = 1,
        size: int = 20,
        driver_code: str = "",
        driver_name: str = "",
        phone: str = "",
        email: str = "",
        vehicle: str = "",
        vehicle_type: str = "",
        operability: str = "",
        sort: str = "ASC",
    ) -> dict[str, Any]:
        """Listado de choferes y filtros del reporte Web 082."""
        filters = [
            {"alias": "carrierId", "value": str(self.carrier_id)},
            {"alias": "driverCode", "value": driver_code},
            {"alias": "driverName", "value": driver_name},
            {"alias": "phone", "value": phone},
            {"alias": "email", "value": email},
            {"alias": "vehicle", "value": vehicle},
            {"alias": "vehicleType", "value": vehicle_type},
            {"alias": "operability", "value": operability},
        ]
        # El Web de MAGIIS usa un tamaño de 1.000.000 para este reporte.
        # Si se pide el tamaño de la tabla (20/50/100), MAGIIS devuelve solo
        # esa página y el panel termina mostrando 50 aunque el total real sea
        # 184. Traemos el reporte completo y paginamos acá.
        payload = self._post_report(
            "082", filters=filters, page=1, size=1000000, column="", sort=sort
        )
        page_data = payload.get("page", payload)
        content = page_data.get("content", []) if isinstance(page_data, Mapping) else []
        items = []
        for entry in content:
            row = entry.get("row", entry) if isinstance(entry, Mapping) else {}
            items.append({
                "driverUserId": row.get("Col1"),
                "driverIdForCarrier": row.get("Col2"),
                "driverName": row.get("Col3"),
                "phoneNumber": row.get("Col4"),
                "driverUserEmail": row.get("Col5"),
                "vehicleId": row.get("Col6"),
                "vehicleBrand": row.get("Col7"),
                "vehicleModel": row.get("Col8"),
                "vehicleDomain": row.get("Col9"),
                "vehicleType": row.get("Col10"),
                "operability": row.get("Col11"),
                "rating": row.get("Col12"),
                "vehicleDescription": row.get("Col13"),
            })
        start = max(0, page - 1) * size
        total = page_data.get("totalElements", len(items))
        return {
            "items": items[start:start + size],
            "total": total,
            "page": page,
            "size": size,
        }

    def driver_detail_web(self, driver_user_id: int) -> dict[str, Any]:
        """Detalle exacto usado por la pantalla Web de choferes."""
        return self._get(f"/drivers/{driver_user_id}")

    def driver_report_079(self, *, page: int = 1, size: int = 20) -> dict[str, Any]:
        return self._post_report(
            "079",
            filters=[{"alias": "carrierId", "value": str(self.carrier_id)}],
            page=page,
            size=size,
            column="id",
            sort="DESC",
        )

    def driver_report_108d(
        self, *, page: int = 1, size: int = 20, vehicle_id: str = ""
    ) -> dict[str, Any]:
        return self._post_report(
            "108D",
            filters=[
                {"alias": "carrierId", "value": str(self.carrier_id)},
                {"alias": "vehicleId", "value": vehicle_id},
                {"alias": "driverCom", "value": ""},
                {"alias": "driverComByCost", "value": ""},
            ],
            page=page,
            size=size,
            column="id",
            sort="DESC",
        )

    def driver_report_114(self, *, page: int = 1, size: int = 20) -> dict[str, Any]:
        return self._post_report(
            "114",
            filters=[{"alias": "CARRIERID", "value": str(self.carrier_id)}],
            page=page,
            size=size,
            column="id",
            sort="DESC",
        )

    def unlinked_vehicle_owners(self, *, find: str = "") -> list[dict[str, Any]]:
        return self._get(
            f"/carriers/{self.carrier_id}/vehicleOwners/unlinkedToDriver",
            params={"find": find},
        )

    def carrier_vehicles(self, *, domain: str = "") -> list[dict[str, Any]]:
        return self._get(
            f"/carriers/{self.carrier_id}/vehicles", params={"domain": domain}
        )

    def vehicle_types_web(self) -> list[dict[str, Any]]:
        return self._get(f"/vehicles/{self.carrier_id}/types")

    def vehicle_detail_web(self, vehicle_id: int) -> dict[str, Any]:
        return self._get(f"/vehicles/{vehicle_id}")

    def vehicle_drivers(self, vehicle_id: int) -> list[dict[str, Any]]:
        return self._get(
            f"/carriers/{self.carrier_id}/vehicleOwners/vehicle/{vehicle_id}/drivers"
        )

    def checking_account_driver_movements(self) -> list[dict[str, Any]]:
        return self._get(
            f"/checking-accounts/carrier/{self.carrier_id}/movements",
            params={"type": "driver", "carrierId": self.carrier_id},
        )

    def checking_account_client_totals(self) -> dict[str, Any]:
        return self._get(f"/checking-accounts/carrier/{self.carrier_id}/clients/totals")

    def checking_account_clients(
        self, *, page: int = 1, size: int = 50, find: str = ""
    ) -> dict[str, Any]:
        return self._get(
            f"/checking-accounts/carrier/{self.carrier_id}/clients/paginated",
            params={
                "page": page,
                "size": size,
                "column": "id",
                "sort": "ASC",
                "find": find,
            },
        )

    def countries(self) -> list[dict[str, Any]]:
        return self._get("/multiregion/countries")

    def states(self, country_id: int) -> list[dict[str, Any]]:
        return self._get(f"/multiregion/states/countryId/{country_id}")

    def documents(self, entity: str) -> list[dict[str, Any]]:
        if entity not in {"DRIVER", "VEHICLE"}:
            raise ValueError("entity must be DRIVER or VEHICLE")
        return self._get(f"/data/documents/{entity}")

    def driver_account_movements(
        self, driver_user_id: int, *, page: int = 1, size: int = 50,
        start_date: str = "", end_date: str = ""
    ) -> dict[str, Any]:
        return self._get(
            f"/carriers/{self.carrier_id}/drivers/{driver_user_id}/checkingAccountMovements/paginated",
            params={
                "page": page,
                "size": size,
                "column": "creationDate",
                "sort": "DESC",
                "startDate": start_date,
                "endDate": end_date,
            },
        )

    def travel_history_page(self, *, page: int = 1, size: int = 50, date_from: str = "", date_to: str = "", state: str = "", find: str = "") -> dict[str, Any]:
        """Histórico del panel Web de MAGIIS: reporte 023."""
        def web_date(value: str) -> str:
            parts = value.split('-')
            return '/'.join(reversed(parts)) if len(parts) == 3 else value
        filters = [
            {"alias": "carrieraccount_Id", "value": str(self.carrier_id)},
            {"alias": "owner_id", "value": ""},
            {"alias": "dateFrom", "value": web_date(date_from)},
            {"alias": "dateTo", "value": web_date(date_to)},
            {"alias": "STATE", "value": state},
            {"alias": "CHANNEL", "value": ""},
            {"alias": "PAYMENTMETHOD", "value": ""},
            {"alias": "SEARCH_STRING", "value": f"%{find}%"},
        ]
        return self._post_report("023", filters=filters, page=page, size=size, column="3", sort="DESC")

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

    def driver_dashboard(
        self, *, page: int = 1, size: int = 999999, vehicle_id_switch: bool = False
    ) -> dict[str, Any]:
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
                "vehicleIdSwitch": str(vehicle_id_switch).lower(),
            },
        )

    def driver(self, driver_user_id: int) -> dict[str, Any]:
        return self._get(
            f"/carriers/{self.carrier_id}/drivers/{driver_user_id}/dashboard/driver"
        )

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

    def create_vehicle(self, payload: Mapping[str, Any]) -> Any:
        return self._mutate("POST", "/vehicles", payload=payload)

    def create_driver(self, payload: Mapping[str, Any]) -> Any:
        return self._mutate("POST", "/drivers", payload=payload)

    def vehicle_owner_page(self, *, page: int = 1, size: int = 50, find: str = "") -> dict[str, Any]:
        return self._get(f"/carriers/{self.carrier_id}/vehicleOwners/paginated", params={"page": page, "size": size, "column": "id", "sort": "ASC", "find": find})

    def vehicle_owner(self, owner_id: int) -> dict[str, Any]:
        return self._get(f"/carriers/{self.carrier_id}/vehicleOwners/{owner_id}")

    def create_vehicle_owner(self, payload: Mapping[str, Any]) -> Any:
        return self._mutate("POST", f"/carriers/{self.carrier_id}/vehicleOwners", payload=payload)

    def update_vehicle_owner(self, owner_id: int, payload: Mapping[str, Any]) -> Any:
        return self._mutate("PUT", f"/carriers/{self.carrier_id}/vehicleOwners/{owner_id}", payload=payload)

    def update_vehicle(self, vehicle_id: int, payload: Mapping[str, Any]) -> Any:
        return self._mutate("PUT", f"/vehicles/{vehicle_id}", payload=payload)

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
        return self._get(
            f"/carriers/{self.carrier_id}/drivers/totalDrivers",
            params={"driverId": ""},
        )

    def operational_report(self) -> dict[str, Any]:
        """Replica el POST del reporte 042 usado por Control operacional."""
        payload = {
            "page": 1,
            "column": "",
            "sort": "DESC",
            "size": 1000000,
            "reportCode": "042",
            "filters": [{"alias": "carrierId", "value": str(self.carrier_id)}],
        }
        for attempt in range(2):
            response = self.client.post(
                "/data/genericReport/042/paginated",
                params={"page": 1, "size": 1000000, "column": "", "sort": "DESC"},
                json=payload,
                headers={"Authorization": f"Bearer {self.session['token']}"},
            )
            if response.status_code not in (401, 403) or attempt == 1:
                response.raise_for_status()
                return response.json()
            self.session = magiis_web.login(self.client)
        raise AssertionError("unreachable")

    def chats_all(self) -> dict[str, Any]:
        """Replica la consulta MMC de chats, sin mutar conversaciones."""
        for attempt in range(2):
            response = self.mmc_client.get(
                "/mmc/chats/all",
                params={"carrierId": self.carrier_id, "role": "CARRIER"},
                headers={
                    "Authorization": f"Bearer {self.session['token']}",
                    "Accept": "application/json",
                },
            )
            if response.status_code not in (401, 403) or attempt == 1:
                response.raise_for_status()
                return response.json()
            self.session = magiis_web.login(self.client)
        raise AssertionError("unreachable")

    def drivers_for_combo(self) -> list[dict[str, Any]]:
        return self._get(f"/carriers/{self.carrier_id}/drivers/driversForCombo")

    def vehicles(self) -> list[dict[str, Any]]:
        return self._get(f"/carriers/{self.carrier_id}/vehicles")

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
