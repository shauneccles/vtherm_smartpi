"""Discovery form schema + submission tests."""

import voluptuous as vol

from custom_components.vtherm_smartpi.config_flow import (
    DISCOVERY_POLICY_SUFFIX,
    build_discovery_schema,
    endpoint_field_options,
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
