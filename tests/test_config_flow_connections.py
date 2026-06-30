"""Tests for per-aperture connection validation."""

from custom_components.vtherm_smartpi.config_flow import validate_connection_entry
from custom_components.vtherm_smartpi.const import (
    CONF_CONN_TARGET_KIND,
    CONF_CONN_APERTURE_SENSOR,
    CONF_CONN_NEIGHBOR_VTHERM,
    CONF_CONN_NEIGHBOR_TEMP_SENSOR,
    CONN_TARGET_ROOM,
    CONN_TARGET_SENSOR,
    CONN_TARGET_OUTSIDE,
)
from custom_components.vtherm_smartpi.config_flow import ERROR_CONNECTION_INCOMPLETE


def test_outside_needs_only_aperture():
    assert validate_connection_entry({
        CONF_CONN_TARGET_KIND: CONN_TARGET_OUTSIDE,
        CONF_CONN_APERTURE_SENSOR: "binary_sensor.window_1",
    }) is None


def test_outside_missing_aperture_incomplete():
    assert validate_connection_entry({
        CONF_CONN_TARGET_KIND: CONN_TARGET_OUTSIDE,
    }) == ERROR_CONNECTION_INCOMPLETE


def test_sensor_needs_temp_and_aperture():
    assert validate_connection_entry({
        CONF_CONN_TARGET_KIND: CONN_TARGET_SENSOR,
        CONF_CONN_NEIGHBOR_TEMP_SENSOR: "sensor.hall",
        CONF_CONN_APERTURE_SENSOR: "binary_sensor.door_hall",
    }) is None
    assert validate_connection_entry({
        CONF_CONN_TARGET_KIND: CONN_TARGET_SENSOR,
        CONF_CONN_APERTURE_SENSOR: "binary_sensor.door_hall",
    }) == ERROR_CONNECTION_INCOMPLETE


def test_room_needs_neighbor_and_aperture():
    assert validate_connection_entry({
        CONF_CONN_TARGET_KIND: CONN_TARGET_ROOM,
        CONF_CONN_NEIGHBOR_VTHERM: "climate.bedroom",
        CONF_CONN_APERTURE_SENSOR: "binary_sensor.door_ab",
    }) is None


def test_aperture_reuse_rejected():
    from custom_components.vtherm_smartpi.config_flow import _aperture_already_used
    from custom_components.vtherm_smartpi.const import CONF_CONN_APERTURE_SENSOR
    existing = [{CONF_CONN_APERTURE_SENSOR: "binary_sensor.door"}]
    assert _aperture_already_used("binary_sensor.door", existing) is True
    assert _aperture_already_used("binary_sensor.other", existing) is False
    # legacy door-sensor key also counts
    assert _aperture_already_used(
        "binary_sensor.leg", [{"connection_door_sensor": "binary_sensor.leg"}]) is True
