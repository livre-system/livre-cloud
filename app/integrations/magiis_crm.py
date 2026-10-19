"""Compatibilidad histórica deshabilitada.

No hay cliente CRM externo en el circuito de Livre Cloud.
"""


class MagiisCrmReader:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("El lector externo está deshabilitado; usar tablas de Livre Cloud")
