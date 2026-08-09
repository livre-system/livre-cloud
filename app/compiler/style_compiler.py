from datetime import datetime, timezone


def compile_style_context(sources: dict) -> dict:
    return {
        "type": "style",
        "sources": {
            "style": sources.get("style", ""),
        },
        "compiled_at": datetime.now(timezone.utc).isoformat(),
    }
