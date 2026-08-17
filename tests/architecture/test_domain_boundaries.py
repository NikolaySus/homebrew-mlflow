from pathlib import Path


def test_domain_does_not_import_framework_or_adapter_packages() -> None:
    root = Path("packages/domain/src")
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    forbidden = ("fastapi", "pydantic", "sqlalchemy", "mlflow", "boto", "gitlab")
    assert not any(f"import {name}" in source or f"from {name}" in source for name in forbidden)
