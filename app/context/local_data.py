"""Consultas de solo lectura sobre las entidades propias de Livre Cloud."""
from __future__ import annotations

import re
import unicodedata

from app.core.database import get_cursor


_TABLES = {
    "livre_users", "livre_driver_accounts", "livre_driver_vehicles",
    "livre_crm_passengers", "livre_crm_vehicle_owners", "livre_crm_vehicles",
    "livre_crm_commissions", "livi_trips", "livi_whatsapp_quotes",
    "livre_driver_shift_closures", "livre_quote_profiles",
}

_STOPWORDS = {
    "necesitaria", "necesito", "informacion", "informacion", "sobre", "datos",
    "consulta", "consultar", "buscar", "busco", "dame", "decime", "quiero",
    "toda", "todas", "base", "datos", "los", "las", "del", "de", "un", "una",
    "por", "favor", "que", "hay", "con", "para", "en", "el", "la", "y", "completa", "completo", "completos", "completas", "toda", "todo",
    "chofer", "choferes", "conductor", "conductores", "pasajero", "pasajeros", "cliente", "clientes",
    "vehiculo", "vehiculos", "auto", "autos", "viaje", "viajes", "propietario", "dueno", "duenos",
    "comision", "comisiones", "tarifa", "tarifas", "cotizacion", "cotizaciones", "base", "registros",
    "enlistame", "listame", "listar", "lista", "listarme", "mostrame", "mostrar", "ultimo", "ultimos", "ultima", "ultimas",
}


def normalize_query(query: str) -> str:
    value = unicodedata.normalize("NFD", query or "")
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def is_local_data_query(query: str) -> bool:
    normalized = normalize_query(query)
    if bool(re.search(
        r"\b(chofer|choferes|conductor|conductores|pasajero|pasajeros|cliente|clientes|"
        r"vehiculo|vehiculos|auto|autos|viaje|viajes|propietario|dueno|duenos|"
        r"comision|comisiones|tarifa|tarifas|cotizacion|cotizaciones|base|registros|"
        r"datos operativos|informacion operativa)\b",
        normalized,
    )):
        return True
    # En una conversación, el turno siguiente suele ser sólo un nombre:
    # "necesito un chofer" -> "Fabio Pérez".
    meaningful = [t for t in normalized.split() if t not in _STOPWORDS]
    return len(meaningful) == 2 and all(t.isalpha() for t in meaningful)


def _terms(query: str) -> list[str]:
    return [
        t for t in normalize_query(query).split()
        if len(t) >= 3 and not t.isdigit() and t not in _STOPWORDS
    ]


def _table_set(cur) -> set[str]:
    cur.execute(
        """SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'public' AND table_name = ANY(%s)""",
        (list(_TABLES),),
    )
    return {row["table_name"] for row in cur.fetchall()}


def _condition(columns: tuple[str, ...], terms: list[str]) -> tuple[str, list[str]]:
    if not terms:
        return "TRUE", []
    clauses, params = [], []
    for term in terms:
        clauses.append("(" + " OR ".join(f"{column} ILIKE %s" for column in columns) + ")")
        params.extend([f"%{term}%"] * len(columns))
    return " AND ".join(clauses), params


def _rows(cur, table: str, select: str, columns: tuple[str, ...], terms: list[str], limit: int = 10):
    condition, params = _condition(columns, terms)
    cur.execute(f"SELECT {select} FROM {table} WHERE {condition} ORDER BY 1 LIMIT %s", [*params, limit])
    return cur.fetchall()


def _fmt(value) -> str:
    return "no informado" if value is None or value == "" else str(value)


def get_local_data_context(query: str) -> str:
    """Devuelve contexto acotado; nunca ejecuta SQL generado por el usuario."""
    terms = _terms(query)
    normalized = normalize_query(query)
    try:
        with get_cursor() as cur:
            available = _table_set(cur)
            parts = ["FUENTE OPERATIVA AUTORITATIVA: registros propios de Livre Cloud."]

            if not terms and ("base" in normalized or "registros" in normalized or "datos" in normalized):
                for table in sorted(available):
                    cur.execute(f"SELECT COUNT(*) AS total FROM {table}")
                    parts.append(f"{table}: {cur.fetchone()['total']} registros.")
                return "\n".join(parts)

            if {"livre_driver_accounts", "livre_users"} <= available and re.search(r"\b(chofer|conductor)\b", normalized):
                condition, params = _condition(("u.name", "u.username", "u.email", "a.user_id"), terms)
                cur.execute(f"""SELECT a.user_id AS id, u.name, u.username, u.email,
                    a.active, a.operational_status, a.location_updated_at
                    FROM livre_driver_accounts a JOIN livre_users u ON u.id = a.user_id
                    WHERE {condition} ORDER BY u.name NULLS LAST, a.user_id LIMIT %s""", [*params, 10])
                rows = cur.fetchall()
                if rows:
                    for row in rows:
                        parts.append("Chofer: " + "; ".join([
                            f"nombre={_fmt(row['name'] or row['username'])}", f"ID Livre={_fmt(row['id'])}",
                            f"email={_fmt(row['email'])}", f"activo={_fmt(row['active'])}",
                            f"estado={_fmt(row['operational_status'])}", f"última ubicación={_fmt(row['location_updated_at'])}",
                        ]))
                    return "\n".join(parts)

            if {"livre_driver_accounts", "livre_users"} <= available and len(terms) == 2:
                condition, params = _condition(("u.name", "u.username", "u.email", "a.user_id"), terms)
                cur.execute(f"""SELECT a.user_id AS id, u.name, u.username, u.email,
                    a.active, a.operational_status, a.location_updated_at
                    FROM livre_driver_accounts a JOIN livre_users u ON u.id = a.user_id
                    WHERE {condition} ORDER BY u.name NULLS LAST, a.user_id LIMIT %s""", [*params, 10])
                rows = cur.fetchall()
                if rows:
                    for row in rows:
                        parts.append("Registro de persona/chofer: " + "; ".join([
                            f"nombre={_fmt(row['name'] or row['username'])}", f"ID Livre={_fmt(row['id'])}",
                            f"email={_fmt(row['email'])}", f"activo={_fmt(row['active'])}",
                            f"estado={_fmt(row['operational_status'])}",
                        ]))
                    return "\n".join(parts)

            if {"livre_driver_accounts", "livre_users"} <= available and re.search(r"\b(viaje|viajes)\b", normalized) and "livi_trips" in available:
                condition, params = _condition(("u.name", "u.username", "u.email", "t.assigned_driver_user_id", "t.tracking_driver_name"), terms)
                cur.execute(f"""SELECT t.id, t.origen, t.destino, t.status, t.tracking_driver_name,
                    t.tracking_plate, t.created_at, t.updated_at
                    FROM livi_trips t
                    LEFT JOIN livre_driver_accounts a ON a.user_id = t.assigned_driver_user_id
                    LEFT JOIN livre_users u ON u.id = a.user_id
                    WHERE {condition}
                    ORDER BY t.created_at DESC NULLS LAST, t.id DESC LIMIT %s""", [*params, 50])
                rows = cur.fetchall()
                if rows:
                    for row in rows:
                        parts.append("Viaje: " + "; ".join([
                            f"ID={_fmt(row['id'])}", f"origen={_fmt(row['origen'])}", f"destino={_fmt(row['destino'])}",
                            f"estado={_fmt(row['status'])}", f"chofer={_fmt(row['tracking_driver_name'])}",
                            f"patente={_fmt(row['tracking_plate'])}", f"creado={_fmt(row['created_at'])}",
                        ]))
                    return "\n".join(parts)

            entity_specs = [
                ("livre_crm_passengers", ("id", "first_name", "last_name", "phone", "email"), ("first_name", "last_name", "phone", "email"), "Pasajero"),
                ("livre_crm_vehicle_owners", ("id", "first_name", "last_name", "phone", "email", "city"), ("first_name", "last_name", "phone", "email", "identification", "city"), "Propietario"),
                ("livre_crm_vehicles", ("id", "plate", "brand", "model", "year", "color", "vehicle_type", "owner_id"), ("plate", "brand", "model", "color", "vehicle_type"), "Vehículo"),
                ("livi_trips", ("id", "origen", "destino", "whatsapp_user", "status", "final_fare", "created_at"), ("origen", "destino", "whatsapp_user", "status"), "Viaje"),
                ("livre_crm_commissions", ("id", "vehicle", "vehicle_model", "owner", "total", "commission", "commission_percent", "settled_at"), ("vehicle", "vehicle_model", "owner"), "Comisión"),
            ]
            keyword_map = {
                "livre_crm_passengers": "pasajero|cliente", "livre_crm_vehicle_owners": "propietario|dueno|dueño",
                "livre_crm_vehicles": "vehiculo|auto", "livi_trips": "viaje", "livre_crm_commissions": "comision",
            }
            for table, select_cols, search_cols, label in entity_specs:
                if table not in available or not re.search(rf"\b({keyword_map[table]})s?\b", normalized):
                    continue
                rows = _rows(cur, table, ", ".join(select_cols), search_cols, terms)
                for row in rows:
                    parts.append(label + ": " + "; ".join(f"{col}={_fmt(row[col])}" for col in select_cols))
                if rows:
                    return "\n".join(parts)

            return "Consulta operativa: no se encontró un registro coincidente en las entidades propias de Livre Cloud. No inventes datos y pedí un nombre, teléfono, ID, patente o fecha más precisa."
    except Exception:
        return "Consulta operativa: la fuente local de Livre Cloud no está disponible en este momento. No inventes datos ni uses proveedores históricos."
