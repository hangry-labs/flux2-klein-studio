"""Validated generation settings shared by browser, HTTP and MCP."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: str = Field(min_length=1, max_length=4000)
    seed: int = Field(default=12345, ge=0, le=2**63 - 1)
    width: int = Field(default=1024, ge=64, le=1920, multiple_of=16)
    height: int = Field(default=1024, ge=64, le=1920, multiple_of=16)
    steps: int = Field(default=4, ge=1, le=50)
    save_gallery: bool = False
    reference_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    adapter: Literal["pixel-art"] | None = None

    @model_validator(mode="after")
    def adapter_mode(self):
        if self.adapter and self.reference_id:
            raise ValueError(
                "Adapters are currently supported for text-to-image generation only"
            )
        return self

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Describe the image you want to create")
        return value.strip()
