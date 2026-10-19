"""Compatibilidad histórica deshabilitada.

Las cotizaciones nuevas se guardan en livi_whatsapp_quotes y los perfiles
locales de Livre; no se escriben tablas históricas de proveedores.
"""


def persist_quote_response(*args, **kwargs):
    raise RuntimeError("La persistencia externa está deshabilitada; usar Livre Cloud")
