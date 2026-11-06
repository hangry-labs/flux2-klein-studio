"""Persistent agent resolution choices and the packaged benchmark reference."""

import json
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from .presets import Resolution, catalog

REFERENCE = Path(__file__).with_name("data") / "preset-memory.json"


class ResolutionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled_resolutions: list[str]

    @field_validator("enabled_resolutions")
    @classmethod
    def known_unique(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Each resolution must appear only once")
        if any(value not in Resolution._value2member_map_ for value in values):
            raise ValueError("Choose resolution IDs from the catalog")
        return values


class ResolutionPolicy:
    def __init__(self, path, lock):
        self.path, self.lock = path, lock
        self.enabled = {name.value for name in Resolution}
        if path.exists():
            # Invalid persisted policy must not silently re-enable disabled sizes.
            self.enabled = set(
                ResolutionSettings.model_validate_json(
                    path.read_text(encoding="utf-8")
                ).enabled_resolutions
            )
        self.reference = (
            json.loads(REFERENCE.read_text(encoding="utf-8"))
            if REFERENCE.exists()
            else None
        )

    def allowed(self):
        with self.lock:
            return [name.value for name in Resolution if name.value in self.enabled]

    def default(self):
        allowed = self.allowed()
        return "square_big" if "square_big" in allowed else next(iter(allowed), None)

    def catalog(self):
        with self.lock:
            return [row for row in catalog() if row["id"] in self.enabled]

    def choose(self, value=None):
        with self.lock:
            if not self.enabled:
                raise HTTPException(
                    503,
                    "Image generation for agents is disabled: no resolutions are enabled.",
                )
            value = self.default() if value is None else str(value)
            if value not in self.enabled:
                raise HTTPException(
                    400,
                    f"Resolution {value} is disabled in System settings. Refresh the resolution catalog.",
                )
            return Resolution(value)

    def snapshot(self):
        with self.lock:
            measurements = self.reference.get("results", {}) if self.reference else {}
            return {
                "enabled_resolutions": self.allowed(),
                "default": self.default(),
                "reference": {
                    key: value
                    for key, value in self.reference.items()
                    if key != "results"
                }
                if self.reference
                else None,
                "resolutions": [
                    {
                        **row,
                        "family": row["id"].split("_")[0],
                        "enabled": row["id"] in self.enabled,
                        "memory": measurements.get(row["id"]),
                    }
                    for row in catalog()
                ],
            }

    def save(self, options):
        with self.lock:
            selected = set(options.enabled_resolutions)
            ordered = [name.value for name in Resolution if name.value in selected]
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"enabled_resolutions": ordered}, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
            self.enabled = selected
            return self.snapshot()
