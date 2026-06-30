"""Discover a SmartPI room's apertures and candidate neighbour nodes from HA.

Pure core (no Home Assistant) + thin registry adapters. The pure functions take
plain dataclasses so the selection/mapping logic is unit-testable without HA.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..const import (
    CONF_CONN_APERTURE_SENSOR,
    CONF_CONN_APERTURE_TYPE,
    CONF_CONN_DOOR_SENSOR,
    CONF_CONN_NEIGHBOR_TEMP_SENSOR,
    CONF_CONN_NEIGHBOR_VTHERM,
    CONF_CONN_OPEN_POLICY,
    CONF_CONN_TARGET_KIND,
    CONN_TARGET_OUTSIDE,
    CONN_TARGET_ROOM,
    CONN_TARGET_SENSOR,
)

APERTURE_CLASSES = frozenset({"door", "window", "opening", "garage_door"})
VT_PLATFORM = "versatile_thermostat"


@dataclass(frozen=True)
class ApertureRecord:
    """A binary_sensor candidate, flattened from the registries."""

    entity_id: str
    name: str
    effective_device_class: str | None
    effective_area_id: str | None
    platform: str | None


@dataclass(frozen=True)
class DiscoveredAperture:
    """An aperture belonging to the room, ready to wire."""

    aperture_entity_id: str
    name: str
    aperture_type: str  # "door" | "window"
    current: dict | None


def aperture_id_of(conn: dict) -> str | None:
    """Return the aperture sensor id of an existing connection dict (legacy-aware)."""
    return conn.get(CONF_CONN_APERTURE_SENSOR) or conn.get(CONF_CONN_DOOR_SENSOR)


def select_apertures(
    records: list[ApertureRecord], area_id: str, existing: list[dict]
) -> list[DiscoveredAperture]:
    """Pick door/window sensors in *area_id*, excluding VTherm-internal sensors."""
    by_aperture = {aperture_id_of(c): c for c in (existing or []) if aperture_id_of(c)}
    out: list[DiscoveredAperture] = []
    for rec in records:
        if rec.platform == VT_PLATFORM:
            continue
        if rec.effective_device_class not in APERTURE_CLASSES:
            continue
        if rec.effective_area_id != area_id:
            continue
        aperture_type = "window" if rec.effective_device_class == "window" else "door"
        out.append(
            DiscoveredAperture(
                aperture_entity_id=rec.entity_id,
                name=rec.name,
                aperture_type=aperture_type,
                current=by_aperture.get(rec.entity_id),
            )
        )
    return out
