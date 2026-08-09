from datetime import datetime, timezone


def compile_base_context(sources: dict) -> dict:
    now = datetime.now(timezone.utc)
    fecha = now.strftime("%A %d de %B de %Y, %H:%M UTC")

    return {
        "type": "base",
        "sources": {
            "personal_log": sources.get("personal_log", ""),
            "cognitive_base": sources.get("cognitive_base", ""),
            "destilador": sources.get("destilador", ""),
        },
        "fecha_actual": fecha,
        "compiled_at": now.isoformat(),
    }
