"""Pure-core tests for room-network discovery (no Home Assistant)."""

from custom_components.vtherm_smartpi.smartpi.topology import (
    ApertureRecord,
    DiscoveredAperture,
    aperture_id_of,
    select_apertures,
)
from custom_components.vtherm_smartpi.const import (
    CONF_CONN_APERTURE_SENSOR,
    CONF_CONN_DOOR_SENSOR,
    CONF_CONN_TARGET_KIND,
    CONF_CONN_NEIGHBOR_VTHERM,
    CONN_TARGET_ROOM,
)


def _rec(entity_id, dc, area, platform="mqtt", name=None):
    return ApertureRecord(
        entity_id=entity_id,
        name=name or entity_id,
        effective_device_class=dc,
        effective_area_id=area,
        platform=platform,
    )


def test_selects_door_and_window_in_area():
    records = [
        _rec("binary_sensor.bedroom_door", "door", "bedroom"),
        _rec("binary_sensor.bedroom_window", "window", "bedroom"),
        _rec("binary_sensor.kitchen_window", "window", "kitchen"),
    ]
    got = select_apertures(records, "bedroom", existing=[])
    ids = sorted(a.aperture_entity_id for a in got)
    assert ids == ["binary_sensor.bedroom_door", "binary_sensor.bedroom_window"]
    by_id = {a.aperture_entity_id: a for a in got}
    assert by_id["binary_sensor.bedroom_door"].aperture_type == "door"
    assert by_id["binary_sensor.bedroom_window"].aperture_type == "window"


def test_excludes_non_aperture_classes_and_vt_platform():
    records = [
        _rec("binary_sensor.motion", "motion", "bedroom"),
        _rec("binary_sensor.tamper", None, "bedroom"),
        _rec("binary_sensor.bedroom_heating_window_state", "window", "bedroom",
             platform="versatile_thermostat"),
        _rec("binary_sensor.opening", "opening", "bedroom"),
        _rec("binary_sensor.garage", "garage_door", "bedroom"),
    ]
    got = sorted(a.aperture_entity_id for a in select_apertures(records, "bedroom", []))
    assert got == ["binary_sensor.garage", "binary_sensor.opening"]


def test_backreferences_existing_connection():
    records = [_rec("binary_sensor.bedroom_door", "door", "bedroom")]
    existing = [{
        CONF_CONN_APERTURE_SENSOR: "binary_sensor.bedroom_door",
        CONF_CONN_TARGET_KIND: CONN_TARGET_ROOM,
        CONF_CONN_NEIGHBOR_VTHERM: "uid-hall",
    }]
    got = select_apertures(records, "bedroom", existing)
    assert got[0].current == existing[0]


def test_aperture_id_of_handles_legacy_key():
    assert aperture_id_of({CONF_CONN_APERTURE_SENSOR: "binary_sensor.a"}) == "binary_sensor.a"
    assert aperture_id_of({CONF_CONN_DOOR_SENSOR: "binary_sensor.legacy"}) == "binary_sensor.legacy"
    assert aperture_id_of({}) is None


from custom_components.vtherm_smartpi.smartpi.topology import (
    AreaNode,
    VThermNode,
    build_candidate_nodes,
    resolve_effective_area,
)


def test_resolve_effective_area_prefers_entity_then_device():
    assert resolve_effective_area("bedroom", "kitchen") == "bedroom"
    assert resolve_effective_area(None, "kitchen") == "kitchen"
    assert resolve_effective_area(None, None) is None


def test_candidate_nodes_controlled_excludes_self_and_non_smartpi():
    vtherms = [
        VThermNode("uid-self", "Bedroom heating", True),
        VThermNode("uid-play", "Playroom heating", True),
        VThermNode("uid-ac", "Living area AC", False),
    ]
    areas = [
        AreaNode("kitchen", "Kitchen", "sensor.kitchen_sensor_temperature"),
        AreaNode("toilet", "Toilet", None),
    ]
    nodes = build_candidate_nodes(vtherms, areas, self_uid="uid-self")
    assert nodes.controlled == [("vt:uid-play", "Playroom heating")]
    # sensed includes only areas with a temp sensor
    assert nodes.sensed == [("area:sensor.kitchen_sensor_temperature", "Kitchen")]
