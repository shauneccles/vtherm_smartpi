"""Pure-core tests for room-network discovery (no Home Assistant)."""

from custom_components.vtherm_smartpi.smartpi.topology import (
    ENDPOINT_OUTSIDE,
    ENDPOINT_SKIP,
    ApertureRecord,
    AreaNode,
    VThermNode,
    aperture_id_of,
    aperture_row_to_connection,
    build_candidate_nodes,
    endpoint_value_for_current,
    merge_discovered_connections,
    resolve_effective_area,
    select_apertures,
)
from custom_components.vtherm_smartpi.const import (
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


def test_candidate_nodes_sensed_excludes_self_area():
    """build_candidate_nodes must not offer the current room's own area as a sensed endpoint."""
    vtherms = [
        VThermNode("uid-self", "Bedroom heating", True),
    ]
    areas = [
        AreaNode("bedroom", "Bedroom", "sensor.bedroom_temperature"),
        AreaNode("kitchen", "Kitchen", "sensor.kitchen_temperature"),
        AreaNode("toilet", "Toilet", None),
    ]
    nodes = build_candidate_nodes(vtherms, areas, self_uid="uid-self", self_area_id="bedroom")
    # bedroom must be excluded since it is the self area
    assert ("area:sensor.bedroom_temperature", "Bedroom") not in nodes.sensed
    # kitchen must remain
    assert ("area:sensor.kitchen_temperature", "Kitchen") in nodes.sensed
    # toilet (no sensor) must not appear
    assert len(nodes.sensed) == 1


def test_candidate_nodes_self_area_none_keeps_all_areas_with_sensor():
    """When self_area_id is None (default), no area is excluded from sensed."""
    vtherms = []
    areas = [
        AreaNode("bedroom", "Bedroom", "sensor.bedroom_temperature"),
        AreaNode("kitchen", "Kitchen", "sensor.kitchen_temperature"),
    ]
    nodes = build_candidate_nodes(vtherms, areas, self_uid=None)
    assert len(nodes.sensed) == 2


def test_endpoint_value_for_current():
    assert endpoint_value_for_current(None) == ENDPOINT_SKIP
    assert endpoint_value_for_current({CONF_CONN_TARGET_KIND: CONN_TARGET_OUTSIDE}) == "outside"
    assert endpoint_value_for_current(
        {CONF_CONN_TARGET_KIND: CONN_TARGET_ROOM, CONF_CONN_NEIGHBOR_VTHERM: "u1"}
    ) == "vt:u1"
    assert endpoint_value_for_current(
        {CONF_CONN_TARGET_KIND: CONN_TARGET_SENSOR, CONF_CONN_NEIGHBOR_TEMP_SENSOR: "sensor.k"}
    ) == "area:sensor.k"
    # legacy shape (no target_kind, has neighbour vtherm)
    assert endpoint_value_for_current({CONF_CONN_NEIGHBOR_VTHERM: "u9"}) == "vt:u9"
    assert endpoint_value_for_current({CONF_CONN_TARGET_KIND: CONN_TARGET_SENSOR}) == ENDPOINT_SKIP
    assert endpoint_value_for_current({CONF_CONN_TARGET_KIND: CONN_TARGET_ROOM}) == ENDPOINT_SKIP


def test_aperture_row_to_connection_variants():
    assert aperture_row_to_connection("binary_sensor.w", "window", ENDPOINT_SKIP, "model") is None
    assert aperture_row_to_connection("binary_sensor.w", "window", "", "model") is None
    out = aperture_row_to_connection("binary_sensor.w", "window", ENDPOINT_OUTSIDE, "trip_off")
    assert out == {
        CONF_CONN_TARGET_KIND: CONN_TARGET_OUTSIDE,
        CONF_CONN_APERTURE_SENSOR: "binary_sensor.w",
        CONF_CONN_APERTURE_TYPE: "window",
        CONF_CONN_OPEN_POLICY: "trip_off",
    }
    room = aperture_row_to_connection("binary_sensor.d", "door", "vt:uid-hall", "model")
    assert room[CONF_CONN_TARGET_KIND] == CONN_TARGET_ROOM
    assert room[CONF_CONN_NEIGHBOR_VTHERM] == "uid-hall"
    sens = aperture_row_to_connection("binary_sensor.d", "door", "area:sensor.hall", "model")
    assert sens[CONF_CONN_TARGET_KIND] == CONN_TARGET_SENSOR
    assert sens[CONF_CONN_NEIGHBOR_TEMP_SENSOR] == "sensor.hall"


def test_merge_preserves_manual_replaces_discovered_drops_skipped():
    existing = [
        {CONF_CONN_APERTURE_SENSOR: "binary_sensor.manual", CONF_CONN_TARGET_KIND: CONN_TARGET_OUTSIDE},
        {CONF_CONN_APERTURE_SENSOR: "binary_sensor.door", CONF_CONN_TARGET_KIND: CONN_TARGET_ROOM,
         CONF_CONN_NEIGHBOR_VTHERM: "old"},
        {CONF_CONN_APERTURE_SENSOR: "binary_sensor.gone", CONF_CONN_TARGET_KIND: CONN_TARGET_OUTSIDE},
    ]
    discovered_ids = ["binary_sensor.door", "binary_sensor.gone"]
    produced = [
        {CONF_CONN_APERTURE_SENSOR: "binary_sensor.door", CONF_CONN_TARGET_KIND: CONN_TARGET_ROOM,
         CONF_CONN_NEIGHBOR_VTHERM: "new"},
        # "binary_sensor.gone" was set to Skip -> not in produced -> removed
    ]
    merged = merge_discovered_connections(existing, discovered_ids, produced)
    apertures = sorted(c[CONF_CONN_APERTURE_SENSOR] for c in merged)
    assert apertures == ["binary_sensor.door", "binary_sensor.manual"]
    door = next(c for c in merged if c[CONF_CONN_APERTURE_SENSOR] == "binary_sensor.door")
    assert door[CONF_CONN_NEIGHBOR_VTHERM] == "new"  # replaced, not duplicated
