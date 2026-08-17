import json
import re
from pathlib import Path

from fastapi.testclient import TestClient
from homebrew_mlflow.api.main import create_app
from homebrew_mlflow.api.settings import get_settings
from homebrew_mlflow.contracts import load_openapi


def test_committed_openapi_is_deterministic_and_32() -> None:
    first = load_openapi()
    second = load_openapi()
    assert first == second
    assert first["openapi"] == "3.2.0"
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_runtime_routes_are_covered_by_contract() -> None:
    app = create_app()
    contract_paths = set(load_openapi()["paths"])

    def included_paths(routes) -> set[str]:  # type: ignore[no-untyped-def]
        result: set[str] = set()
        for route in routes:
            if getattr(route, "include_in_schema", False) and hasattr(route, "path"):
                result.add(route.path)
            nested = getattr(route, "routes", None)
            original_router = getattr(route, "original_router", None)
            if nested is None and original_router is not None:
                nested = original_router.routes
            if nested is not None:
                result.update(included_paths(nested))
        return result

    runtime_paths = included_paths(app.routes)
    assert runtime_paths == contract_paths


def test_openapi_references_operation_ids_and_path_parameters_are_consistent() -> None:
    contract = load_openapi()
    operation_ids: list[str] = []

    def references(value):  # type: ignore[no-untyped-def]
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "$ref":
                    yield child
                else:
                    yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)

    for reference in references(contract):
        assert isinstance(reference, str) and reference.startswith("#/components/")
        target = contract
        for part in reference.removeprefix("#/").split("/"):
            assert part in target, f"unresolved OpenAPI reference: {reference}"
            target = target[part]

    for path, path_item in contract["paths"].items():
        placeholders = set(re.findall(r"\{([^}]+)\}", path))
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_ids.append(operation["operationId"])
            declared = {
                parameter["name"]
                for parameter in operation.get("parameters", [])
                if parameter.get("in") == "path" and parameter.get("required") is True
            }
            assert declared == placeholders, f"path parameter mismatch for {method} {path}"
    assert len(operation_ids) == len(set(operation_ids))


def test_recommended_release_uses_service_only_index() -> None:
    response = TestClient(create_app()).get("/api/v1/client-releases/recommended")
    assert response.status_code == 200
    body = response.json()
    assert body["release"]["index_url"].endswith("/packages/simple/")
    assert "--default-index" in body["install_commands"]["uv"]
    assert "pypi.org" not in json.dumps(body).lower()
    assert response.headers["X-Request-ID"].startswith("req_")


def test_openapi_endpoint_serves_canonical_contract() -> None:
    response = TestClient(create_app()).get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["openapi"] == "3.2.0"


def test_swagger_ui_is_read_only() -> None:
    response = TestClient(create_app()).get("/docs")

    assert response.status_code == 200
    assert '"supportedSubmitMethods": []' in response.text


def test_readiness_returns_safe_503_when_database_is_unavailable(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("homebrew_mlflow.api.main.database_is_ready", lambda _url: False)
    response = TestClient(create_app()).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_release_manifest_hash_is_advertised(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(
        json.dumps({"sha256": {"homebrew_mlflow-0.1.0-py3-none-any.whl": "a" * 64}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOMEBREW_MLFLOW_CLIENT_RELEASE_MANIFEST", str(manifest))
    get_settings.cache_clear()
    try:
        body = TestClient(create_app()).get("/api/v1/client-releases/recommended").json()
        assert body["release"]["sha256"] == {"homebrew_mlflow-0.1.0-py3-none-any.whl": "a" * 64}
    finally:
        get_settings.cache_clear()
