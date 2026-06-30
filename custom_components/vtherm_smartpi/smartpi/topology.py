"""Discover a SmartPI room's apertures and candidate neighbour nodes from HA.

Pure core (no Home Assistant) + thin registry adapters. The pure functions take
plain dataclasses so the selection/mapping logic is unit-testable without HA.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .device_link import VT_DOMAIN, target_uses_smartpi

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


@dataclass(frozen=True)
class VThermNode:
    unique_id: str
    label: str
    is_smartpi: bool


@dataclass(frozen=True)
class AreaNode:
    area_id: str
    label: str
    temp_sensor: str | None


@dataclass(frozen=True)
class CandidateNodes:
    controlled: list[tuple[str, str]]  # (value="vt:<uid>", label)
    sensed: list[tuple[str, str]]      # (value="area:<temp_sensor>", label)


def resolve_effective_area(
    entity_area_id: str | None, device_area_id: str | None
) -> str | None:
    """HA-standard area resolution: entity area, else the entity's device area."""
    return entity_area_id or device_area_id


def build_candidate_nodes(
    vtherms: list[VThermNode], areas: list[AreaNode], self_uid: str | None
) -> CandidateNodes:
    """Far-endpoint choices: SmartPI VTherms (excl. self) + areas with a temp sensor."""
    controlled = [
        (f"vt:{vt.unique_id}", vt.label)
        for vt in vtherms
        if vt.is_smartpi and vt.unique_id != self_uid
    ]
    sensed = [
        (f"area:{a.temp_sensor}", a.label) for a in areas if a.temp_sensor
    ]
    return CandidateNodes(controlled=controlled, sensed=sensed)


ENDPOINT_SKIP = "skip"
ENDPOINT_OUTSIDE = "outside"


def endpoint_value_for_current(current: dict | None) -> str:
    """Map an existing connection dict to its form-selection value."""
    if not current:
        return ENDPOINT_SKIP
    kind = current.get(CONF_CONN_TARGET_KIND)
    if kind == CONN_TARGET_OUTSIDE:
        return ENDPOINT_OUTSIDE
    if kind == CONN_TARGET_SENSOR:
        sensor = current.get(CONF_CONN_NEIGHBOR_TEMP_SENSOR)
        return f"area:{sensor}" if sensor else ENDPOINT_SKIP
    # room (explicit) or legacy (no target_kind but has a neighbour vtherm)
    neighbor = current.get(CONF_CONN_NEIGHBOR_VTHERM)
    return f"vt:{neighbor}" if neighbor else ENDPOINT_SKIP


def aperture_row_to_connection(
    aperture_entity_id: str, aperture_type: str, endpoint_value: str, policy: str
) -> dict | None:
    """Translate one wired form row into a connection dict (None when skipped)."""
    if endpoint_value == ENDPOINT_SKIP or not endpoint_value:
        return None
    base = {
        CONF_CONN_APERTURE_SENSOR: aperture_entity_id,
        CONF_CONN_APERTURE_TYPE: aperture_type,
        CONF_CONN_OPEN_POLICY: policy,
    }
    if endpoint_value == ENDPOINT_OUTSIDE:
        return {CONF_CONN_TARGET_KIND: CONN_TARGET_OUTSIDE, **base}
    if endpoint_value.startswith("vt:"):
        return {
            CONF_CONN_TARGET_KIND: CONN_TARGET_ROOM,
            CONF_CONN_NEIGHBOR_VTHERM: endpoint_value[len("vt:"):],
            **base,
        }
    if endpoint_value.startswith("area:"):
        return {
            CONF_CONN_TARGET_KIND: CONN_TARGET_SENSOR,
            CONF_CONN_NEIGHBOR_TEMP_SENSOR: endpoint_value[len("area:"):],
            **base,
        }
    return None


def merge_discovered_connections(
    existing: list[dict], discovered_ids: list[str], produced: list[dict]
) -> list[dict]:
    """Replace discovered apertures with *produced*; keep everything else.

    Connections whose aperture is in *discovered_ids* but absent from *produced*
    (set to Skip) are dropped. Non-discovered (manual) connections are preserved.
    """
    discovered = set(discovered_ids)
    kept = [
        c for c in (existing or []) if aperture_id_of(c) not in discovered
    ]
    return kept + list(produced)


# ---------------------------------------------------------------------------
# HA registry adapters (thin wrappers — all logic stays in pure core above)
# ---------------------------------------------------------------------------


def _entity_effective_area(hass, entity_entry) -> str | None:
    """Entity area_id, else its device's area_id."""
    device_area = None
    if entity_entry.device_id:
        device = dr.async_get(hass).async_get(entity_entry.device_id)
        device_area = device.area_id if device else None
    return resolve_effective_area(entity_entry.area_id, device_area)


def _entity_label(entity_entry) -> str:
    return entity_entry.name or entity_entry.original_name or entity_entry.entity_id


def resolve_room_area(hass, vtherm_unique_id: str) -> str | None:
    """The VTherm's area (entity area_id, else device area_id). None if unset."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(CLIMATE_DOMAIN, VT_DOMAIN, vtherm_unique_id)
    if not entity_id:
        return None
    entry = registry.async_get(entity_id)
    if entry is None:
        return None
    return _entity_effective_area(hass, entry)


def discover_room_apertures(hass, area_id: str, existing: list[dict]):
    """Discover this room's door/window sensors from the registries."""
    registry = er.async_get(hass)
    records: list[ApertureRecord] = []
    for entry in registry.entities.values():
        if entry.domain != "binary_sensor":
            continue
        dc = entry.device_class or entry.original_device_class
        records.append(
            ApertureRecord(
                entity_id=entry.entity_id,
                name=_entity_label(entry),
                effective_device_class=dc,
                effective_area_id=_entity_effective_area(hass, entry),
                platform=entry.platform,
            )
        )
    return select_apertures(records, area_id, existing)


def discover_candidate_nodes(hass, self_uid: str | None) -> CandidateNodes:
    """Far-endpoint choices: SmartPI VTherms (excl. self) + areas with a temp sensor."""
    registry = er.async_get(hass)
    vtherms: list[VThermNode] = []
    for entry in registry.entities.values():
        if entry.domain != CLIMATE_DOMAIN or entry.platform != VT_DOMAIN:
            continue
        if not entry.unique_id:
            continue
        vtherms.append(
            VThermNode(
                unique_id=entry.unique_id,
                label=_entity_label(entry),
                is_smartpi=target_uses_smartpi(hass, entry.unique_id),
            )
        )
    areas: list[AreaNode] = []
    for area in ar.async_get(hass).async_list_areas():
        areas.append(
            AreaNode(
                area_id=area.id,
                label=area.name,
                temp_sensor=getattr(area, "temperature_entity_id", None),
            )
        )
    return build_candidate_nodes(vtherms, areas, self_uid)
