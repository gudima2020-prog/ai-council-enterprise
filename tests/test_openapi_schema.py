from backend.main import app


def test_openapi_schema_builds() -> None:
    app.openapi_schema = None

    schema = app.openapi()

    assert schema["openapi"]
    assert schema["info"]["title"] == "AI Studio Enterprise"
    assert isinstance(schema["paths"], dict)
    assert schema["paths"]
