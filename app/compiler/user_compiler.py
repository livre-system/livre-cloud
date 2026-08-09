from datetime import datetime, timezone


def compile_user_context(memory: dict) -> dict:
    return {
        "type": "user",
        "memory": {
            "id": memory.get("id", ""),
            "source": memory.get("source", ""),
            "index": memory.get("index", []),
            "documents": memory.get("documents", []),
        },
        "compiled_at": datetime.now(timezone.utc).isoformat(),
    }
