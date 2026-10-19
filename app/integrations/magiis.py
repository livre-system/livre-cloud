"""Compatibilidad histórica deshabilitada.

Livre Cloud no usa la API pública de MAGIIS. Este módulo conserva los nombres
antiguos para evitar imports rotos, pero no contiene cliente HTTP ni credenciales.
"""


def login() -> None:
    raise RuntimeError("La integración externa está deshabilitada; Livre Cloud es la fuente de verdad")


def trip_create(*args, **kwargs):
    raise RuntimeError("La creación externa está deshabilitada; usar livi_trips")
