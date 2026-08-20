from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_MODEL_SIGNATURE_BYTES = 64 * 1024
MODEL_SIGNATURE_FORMAT = "homebrew-mlflow-signature-v1"

_COLUMN_TYPES = {
    "boolean",
    "integer",
    "long",
    "float",
    "double",
    "string",
    "binary",
    "datetime",
}
_TENSOR_DTYPES = {
    "bool",
    "int8",
    "int16",
    "int32",
    "int64",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "float16",
    "float32",
    "float64",
    "str",
    "bytes",
}


class ModelSignatureReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    format: str = Field(pattern="^homebrew-mlflow-signature-v1$")


class ModelSignature(BaseModel):
    """A committed, portable model interface compatible with MLflow Schema JSON."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1, le=1)
    inputs: list[dict[str, Any]] = Field(min_length=1)
    outputs: list[dict[str, Any]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_schemas(self) -> ModelSignature:
        self._validate_schema(self.inputs, "inputs")
        self._validate_schema(self.outputs, "outputs")
        return self

    @staticmethod
    def _validate_schema(items: list[dict[str, Any]], field: str) -> None:
        kinds: set[str] = set()
        named: set[bool] = set()
        for item in items:
            if item.get("type") == "tensor":
                kinds.add("tensor")
                allowed = {"type", "tensor-spec", "name"}
                if set(item) - allowed:
                    raise ValueError(f"{field} tensor contains unknown fields")
                spec = item.get("tensor-spec")
                if not isinstance(spec, dict) or set(spec) != {"dtype", "shape"}:
                    raise ValueError(f"{field} tensor-spec is invalid")
                if spec.get("dtype") not in _TENSOR_DTYPES:
                    raise ValueError(f"{field} tensor dtype is unsupported")
                shape = spec.get("shape")
                if (
                    not isinstance(shape, list)
                    or not shape
                    or any(not isinstance(value, int) or value < -1 for value in shape)
                ):
                    raise ValueError(f"{field} tensor shape is invalid")
            else:
                kinds.add("column")
                allowed = {"type", "name", "required"}
                if set(item) - allowed or item.get("type") not in _COLUMN_TYPES:
                    raise ValueError(f"{field} column is invalid")
                if "required" in item and not isinstance(item["required"], bool):
                    raise ValueError(f"{field} column required flag is invalid")
                if item.get("required") is False and "name" not in item:
                    raise ValueError(f"{field} optional column must be named")
            if "name" in item and (not isinstance(item["name"], str) or not item["name"]):
                raise ValueError(f"{field} name is invalid")
            named.add("name" in item)
        if len(kinds) != 1:
            raise ValueError(f"{field} cannot mix columns and tensors")
        if len(named) != 1:
            raise ValueError(f"{field} entries must be consistently named")
        if kinds == {"tensor"} and named == {False} and len(items) > 1:
            raise ValueError(f"{field} cannot contain multiple unnamed tensors")


def parse_model_signature(content: bytes) -> tuple[dict[str, Any], str]:
    if len(content) > MAX_MODEL_SIGNATURE_BYTES:
        raise ValueError("model signature exceeds 64 KiB")
    try:
        raw = json.loads(content)
        signature = ModelSignature.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError("invalid model signature") from error
    normalized = signature.model_dump(mode="json", by_alias=True)
    canonical = json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return normalized, hashlib.sha256(canonical).hexdigest()
