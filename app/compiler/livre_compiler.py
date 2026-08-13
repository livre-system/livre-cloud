from datetime import datetime, timezone


def compile_livre_context(base: dict, user: dict, style: dict) -> dict:
    return {
        "type": "livre",
        "contexts": {
            "base": base,
            "user": user,
            "style": style,
        },
        "compiled_at": datetime.now(timezone.utc).isoformat(),
    }
