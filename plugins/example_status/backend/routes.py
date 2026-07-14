from fastapi import APIRouter

router = APIRouter()


@router.get("/status")
def plugin_status() -> dict:
    return {
        "plugin": "example_status",
        "status": "ok",
        "message": "Plugin Loader работает.",
        "package_import": "example_status.backend.routes",
    }
