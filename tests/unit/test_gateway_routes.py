from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "relative_path",
    [
        "deploy/reverse-proxy/Caddyfile",
        "deploy/reverse-proxy/Caddyfile.production",
    ],
)
def test_gateway_routes_api_docs_before_spa_fallback(relative_path: str) -> None:
    config = (ROOT / relative_path).read_text(encoding="utf-8")
    docs = config.index("  handle /docs {")
    fallback = config.index("  handle {", docs)

    assert "reverse_proxy api:8000" in config[docs:fallback]
    assert docs < fallback
