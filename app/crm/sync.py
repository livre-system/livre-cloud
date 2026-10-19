"""Compatibilidad histórica: la sincronización externa quedó retirada.

El CRM operativo de Livre Cloud no importa ni ejecuta este módulo. Se conserva
el entrypoint para callers antiguos, pero nunca abre una sesión externa ni
modifica snapshots históricos.
"""


def sync_all() -> dict:
    return {
        "status": "disabled",
        "source": "livre-cloud",
        "resources": {},
        "message": "La sincronización externa está retirada; Livre Cloud es la fuente de verdad.",
    }
