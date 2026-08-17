from homebrew_mlflow.application import redact_mapping


def test_headers_credentials_and_configured_secret_names_are_redacted_recursively() -> None:
    values = {
        "Authorization": "Bearer secret",
        "nested": {"S3_ACCESS_KEY": "value", "safe": "visible"},
        "CUSTOM_NAME": "also secret",
    }

    redacted = redact_mapping(values, frozenset({"CUSTOM_NAME"}))

    assert redacted == {
        "Authorization": "[REDACTED]",
        "nested": {"S3_ACCESS_KEY": "[REDACTED]", "safe": "visible"},
        "CUSTOM_NAME": "[REDACTED]",
    }
