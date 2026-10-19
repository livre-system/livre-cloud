"""Compatibilidad histórica deshabilitada.

La memoria operativa ya no se construye desde snapshots externos.
"""


def sync_magiis_memory(*args, **kwargs):
    return {"status": "disabled", "source": "livre-cloud", "items": []}
