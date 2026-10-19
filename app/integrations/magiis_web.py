"""Compatibilidad histórica deshabilitada.

El CRM no consulta ni escribe en MAGIIS. Las funciones se conservan sólo para
que imports antiguos fallen de forma explícita y segura.
"""

BASE_URL = ""
TIMEOUT = 0
CARRIER_USER_ID = 0


def _disabled(*args, **kwargs):
    raise RuntimeError("La integración MAGIIS Web está deshabilitada; usar Livre Cloud")


_credentials = _disabled
login = _disabled
service_types = _disabled
create_travel = _disabled
audit_travel = _disabled
cancel_travel = _disabled
resolve_address = _disabled
get_place = _disabled
simulate_all = _disabled
