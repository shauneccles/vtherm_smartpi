"""Discovery form schema + submission tests."""

import voluptuous as vol

from custom_components.vtherm_smartpi.config_flow import (
    DISCOVERY_POLICY_SUFFIX,
    build_discovery_connections,
    build_discovery_schema,
    endpoint_field_options,
)
from custom_components.vtherm_smartpi.const import (
    CONF_CONN_APERTURE_SENSOR,
    CONF_CONN_NEIGHBOR_VTHERM,
    CONF_CONN_TARGET_KIND,
    CONN_TARGET_OUTSIDE,
    CONN_TARGET_ROOM,
)
from custom_components.vtherm_smartpi.smartpi.topology import (
    CandidateNodes,
    DiscoveredAperture,
    ENDPOINT_OUTSIDE,
    ENDPOINT_SKIP,
)


def _nodes():
    return CandidateNodes(
        controlled=[("vt:uid-play", "Playroom heating")],
        sensed=[("area:sensor.kitchen_temp", "Kitchen")],
    )


def test_endpoint_options_have_skip_outside_and_nodes():
    opts = endpoint_field_options(_nodes())
    values = [o["value"] for o in opts]
    assert values == [ENDPOINT_SKIP, ENDPOINT_OUTSIDE, "vt:uid-play", "area:sensor.kitchen_temp"]


def test_schema_has_endpoint_and_policy_per_aperture_with_defaults():
    discovered = [
        DiscoveredAperture("binary_sensor.bedroom_window", "Bedroom window", "window", None),
        DiscoveredAperture(
            "binary_sensor.bedroom_door", "Bedroom door", "door",
            {"target_kind": "outside"},
        ),
    ]
    schema = build_discovery_schema(discovered, _nodes())
    assert isinstance(schema, vol.Schema)
    # Defaults applied from current: window -> skip, door -> outside
    defaults = schema({})
    assert defaults["binary_sensor.bedroom_window"] == ENDPOINT_SKIP
    assert defaults["binary_sensor.bedroom_door"] == ENDPOINT_OUTSIDE
    # A policy field exists per aperture, defaulting to "model"
    assert defaults["binary_sensor.bedroom_window" + DISCOVERY_POLICY_SUFFIX] == "model"


def test_build_discovery_connections_maps_rows():
    discovered = [
        DiscoveredAperture("binary_sensor.win", "Bedroom window", "window", None),
        DiscoveredAperture("binary_sensor.door", "Bedroom door", "door", None),
        DiscoveredAperture("binary_sensor.skip_me", "Spare", "door", None),
    ]
    user_input = {
        "binary_sensor.win": ENDPOINT_OUTSIDE,
        "binary_sensor.win" + DISCOVERY_POLICY_SUFFIX: "trip_off",
        "binary_sensor.door": "vt:uid-play",
        "binary_sensor.door" + DISCOVERY_POLICY_SUFFIX: "model",
        "binary_sensor.skip_me": ENDPOINT_SKIP,
        "binary_sensor.skip_me" + DISCOVERY_POLICY_SUFFIX: "model",
    }
    produced, errors = build_discovery_connections(user_input, discovered, _nodes())
    assert errors == {}
    by_ap = {c[CONF_CONN_APERTURE_SENSOR]: c for c in produced}
    assert set(by_ap) == {"binary_sensor.win", "binary_sensor.door"}
    assert by_ap["binary_sensor.win"][CONF_CONN_TARGET_KIND] == CONN_TARGET_OUTSIDE
    assert by_ap["binary_sensor.door"][CONF_CONN_TARGET_KIND] == CONN_TARGET_ROOM
    assert by_ap["binary_sensor.door"][CONF_CONN_NEIGHBOR_VTHERM] == "uid-play"
