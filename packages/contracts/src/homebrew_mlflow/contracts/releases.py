from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class ClientRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: str = "homebrew-mlflow"
    recommended_version: str
    compatible_versions: str
    requires_python: str
    platforms: list[str]
    index_url: AnyHttpUrl
    sha256: dict[str, str] = Field(default_factory=dict)


class ClientReleaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release: ClientRelease
    install_commands: dict[str, str]
