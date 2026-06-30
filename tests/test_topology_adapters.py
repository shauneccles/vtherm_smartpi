"""HA-registry adapter tests for room-network discovery."""

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.vtherm_smartpi.smartpi.topology import (
    discover_room_apertures,
    resolve_room_area,
)

VT_DOMAIN = "versatile_thermostat"


@pytest.mark.asyncio
async def test_resolve_room_area_uses_device_when_entity_area_missing(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    area_reg = ar.async_get(hass)
    living = area_reg.async_create("Living Area")
    dev_reg = dr.async_get(hass)

    entry = MockConfigEntry(domain=VT_DOMAIN, unique_id="uid-living")
    entry.add_to_hass(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(VT_DOMAIN, "living-dev")},
    )
    dev_reg.async_update_device(device.id, area_id=living.id)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "climate", VT_DOMAIN, "uid-living",
        config_entry=entry, device_id=device.id,
    )  # entity has no area_id of its own -> falls back to device area

    assert resolve_room_area(hass, "uid-living") == living.id


@pytest.mark.asyncio
async def test_discover_room_apertures_filters_by_area_and_class(hass):
    area_reg = ar.async_get(hass)
    bedroom = area_reg.async_create("Bedroom")
    ent_reg = er.async_get(hass)

    door = ent_reg.async_get_or_create("binary_sensor", "mqtt", "door-1")
    ent_reg.async_update_entity(door.entity_id, area_id=bedroom.id, device_class="door")
    motion = ent_reg.async_get_or_create("binary_sensor", "mqtt", "motion-1")
    ent_reg.async_update_entity(motion.entity_id, area_id=bedroom.id, device_class="motion")

    got = discover_room_apertures(hass, bedroom.id, existing=[])
    assert [a.aperture_entity_id for a in got] == [door.entity_id]
    assert got[0].aperture_type == "door"
