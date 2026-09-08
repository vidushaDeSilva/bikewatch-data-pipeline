from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# API JSON
#    ↓
# StationEnvelope
#    ↓
# StationData
#    ↓
# list of stations
#    ↓
# StationInformation or StationStatus

class SourceModel(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        allow_inf_nan=False,
    )


class StationInformation(SourceModel):
    station_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    capacity: int = Field(ge=0, strict=True)


class StationStatus(SourceModel):
    station_id: str = Field(min_length=1)
    num_bikes_available: int = Field(ge=0, strict=True)
    num_docks_available: int = Field(ge=0, strict=True)
    num_bikes_disabled: int | None = Field(
        default=None, ge=0, strict=True
    )
    num_docks_disabled: int | None = Field(
        default=None, ge=0, strict=True
    )
    is_installed: int = Field(ge=0, le=1, strict=True)
    is_renting: int = Field(ge=0, le=1, strict=True)
    is_returning: int = Field(ge=0, le=1, strict=True)
    last_reported: int = Field(ge=0, strict=True)


class StationData(SourceModel):
    stations: list[Any]


class StationEnvelope(SourceModel):
    version: Literal["1.1"]
    last_updated: int = Field(gt=0, strict=True)
    ttl: int = Field(ge=0, strict=True)
    data: StationData