from app.context.local_data import is_local_data_query


def test_structured_queries_cover_extended_operational_entities():
    assert is_local_data_query("mostrame las cotizaciones")
    assert is_local_data_query("que vehiculos tienen los conductores")
    assert is_local_data_query("dame los cierres de turno")
    assert is_local_data_query("cuales son los perfiles de tarifa")
