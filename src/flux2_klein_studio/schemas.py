"""Validated generation settings shared by browser, HTTP and MCP."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .adapters import ADAPTERS, AnyAdapter, edit_details


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: str = Field(default="", max_length=4000)
    seed: int = Field(default=12345, ge=0, le=2**63 - 1)
    width: int = Field(default=1024, ge=64, le=1920, multiple_of=16)
    height: int = Field(default=1024, ge=64, le=1920, multiple_of=16)
    steps: int = Field(default=4, ge=1, le=50)
    save_gallery: bool = False
    reference_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    reference_ids: list[str] | None = Field(default=None, min_length=1, max_length=4)
    adapter: AnyAdapter | None = None

    @model_validator(mode="after")
    def adapter_mode(self):
        if self.reference_ids is not None:
            import re

            if self.reference_id or any(
                not re.fullmatch(r"[a-f0-9]{32}", identifier)
                for identifier in self.reference_ids
            ):
                raise ValueError("Use reference_id or one to four valid reference_ids")
            if self.adapter and len(self.reference_ids) > 1:
                raise ValueError(
                    "Multiple references currently support base-model Refine only"
                )
        has_reference = bool(self.reference_id or self.reference_ids)
        if ADAPTERS.get(self.adapter, {}).get("mode") == "edit":
            self.prompt = edit_details(self.prompt, self.adapter)
        else:
            self.nonblank(self.prompt)
        if self.adapter:
            editing = ADAPTERS[self.adapter].get("mode") == "edit"
            if editing and not has_reference:
                raise ValueError("This editing adapter requires a reference image")
            if not editing and has_reference:
                raise ValueError(
                    "This style adapter supports text-to-image generation only"
                )
        return self

    @field_validator("prompt")
    @classmethod
    def strip_prompt(cls, value):
        return value.strip()

    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Describe the image you want to create")
        return value.strip()
