from datetime import datetime, timezone


def compile_hash_context(base: dict, user: dict, style: dict) -> dict:
    return {
        "type": "hash",
        "contexts": {
            "base": base,
            "user": user,
            "style": style,
        },
        "compiled_at": datetime.now(timezone.utc).isoformat(),
    }
