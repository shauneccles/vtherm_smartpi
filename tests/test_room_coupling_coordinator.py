"""Tests for the RoomCouplingCoordinator and per-room views."""

from custom_components.vtherm_smartpi.smartpi.room_coupling import (
    EdgeConfig,
    ResolvedEdge,
    RoomCouplingCoordinator,
    TARGET_ROOM,
    TARGET_OUTSIDE,
    TARGET_SENSOR,
    build_edge_configs,
)
from custom_components.vtherm_smartpi.const import (
    CONF_CONN_NEIGHBOR_VTHERM,
    CONF_CONN_DOOR_SENSOR,
    CONF_CONN_TARGET_KIND,
    CONF_CONN_APERTURE_SENSOR,
    CONF_CONN_APERTURE_TYPE,
    CONF_CONN_OPEN_POLICY,
    CONF_CONN_NEIGHBOR_TEMP_SENSOR,
)


class _FakeState:
    def __init__(self, state):
        self.state = state


class _FakeStates:
    def __init__(self):
        self._d = {}

    def set(self, entity_id, state):
        self._d[entity_id] = _FakeState(state)

    def get(self, entity_id):
        return self._d.get(entity_id)


class _FakeHass:
    def __init__(self):
        self.states = _FakeStates()


def _snap(t_int, power, available=True):
    return {
        "t_int": t_int,
        "text": 5.0,
        "on_percent": 0.5,
        "power_w": power,
        "available": available,
    }


def test_edge_dedup_one_sided_declaration():
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B", aperture_entity_id="binary_sensor.door")])
    coord.register_room("B", [])  # neighbour declares nothing
    assert len(coord._edges) == 1


def test_door_gate_and_power_aggregation():
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    va = coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B", aperture_entity_id="binary_sensor.door")])
    vb = coord.register_room("B", [])
    va.publish(_snap(21.0, 100.0))
    vb.publish(_snap(23.0, 150.0))

    hass.states.set("binary_sensor.door", "off")
    assert va.any_open() is False
    assert coord.component_power_w("A") == 100.0  # isolated -> self only

    hass.states.set("binary_sensor.door", "on")
    assert va.any_open() is True
    open_edges = va.open_edges()
    assert len(open_edges) == 1
    assert open_edges[0].neighbor_temp == 23.0
    assert coord.component_power_w("A") == 250.0  # whole component


def test_unknown_door_state_fails_closed():
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    va = coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B", aperture_entity_id="binary_sensor.door")])
    coord.register_room("B", []).publish(_snap(23.0, 150.0))
    va.publish(_snap(21.0, 100.0))
    # No state set for the door -> treated as closed.
    assert va.any_open() is False


def test_unavailable_neighbor_is_isolated():
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    va = coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B", aperture_entity_id="binary_sensor.door")])
    vb = coord.register_room("B", [])
    va.publish(_snap(21.0, 100.0))
    vb.publish(_snap(None, 150.0, available=False))
    hass.states.set("binary_sensor.door", "on")
    # Door physically open but neighbour unavailable: isolated for the FOLD
    # (open_edges empty), yet fail safe to COUPLED so base a/b learning freezes
    # (any_open True). Power BFS still excludes the dead node.
    assert va.open_edges() == []
    assert va.any_open() is True
    assert coord.component_power_w("A") == 100.0


def test_late_neighbor_resolution():
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    va = coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B", aperture_entity_id="binary_sensor.door")])
    hass.states.set("binary_sensor.door", "on")
    va.publish(_snap(21.0, 100.0))
    # B not registered yet -> aperture open (frozen, fail-safe) but not foldable.
    assert va.any_open() is True
    assert va.open_edges() == []
    vb = coord.register_room("B", [])
    vb.publish(_snap(23.0, 150.0))
    assert va.open_edges() != []         # now resolvable for the fold


def test_unregister_drops_edges():
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B", aperture_entity_id="binary_sensor.door")])
    coord.register_room("B", [])
    coord.unregister_room("A")
    assert coord._edges == {}
    assert "A" not in coord._nodes


def test_reregister_replaces_own_edges_keeps_neighbor_declared():
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B", aperture_entity_id="binary_sensor.door")])
    coord.register_room("B", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="A", aperture_entity_id="binary_sensor.door")])
    # A drops its declaration; B still declares the edge -> it survives.
    coord.register_room("A", [])
    assert len(coord._edges) == 1


def test_edge_id_room_vs_aperture():
    room = EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B",
                      aperture_entity_id="binary_sensor.door_ab")
    out = EdgeConfig(target_kind=TARGET_OUTSIDE,
                     aperture_entity_id="binary_sensor.window_1")
    assert room.edge_id == "B"
    assert out.edge_id == "binary_sensor.window_1"


def test_build_edge_configs_legacy_shape():
    """Legacy {neighbor_vtherm, door_sensor} -> room/door/model."""
    raw = [{CONF_CONN_NEIGHBOR_VTHERM: "B",
            CONF_CONN_DOOR_SENSOR: "binary_sensor.door_ab"}]
    edges, ids = build_edge_configs(raw)
    assert len(edges) == 1
    e = edges[0]
    assert e.target_kind == TARGET_ROOM
    assert e.neighbor_uid == "B"
    assert e.aperture_entity_id == "binary_sensor.door_ab"
    assert e.aperture_type == "door"
    assert e.open_policy == "model"
    assert ids == {"B"}


def test_build_edge_configs_new_shapes():
    raw = [
        {CONF_CONN_TARGET_KIND: TARGET_OUTSIDE,
         CONF_CONN_APERTURE_SENSOR: "binary_sensor.window_1",
         CONF_CONN_APERTURE_TYPE: "window",
         CONF_CONN_OPEN_POLICY: "trip_off"},
        {CONF_CONN_TARGET_KIND: TARGET_SENSOR,
         CONF_CONN_NEIGHBOR_TEMP_SENSOR: "sensor.hall_temp",
         CONF_CONN_APERTURE_SENSOR: "binary_sensor.door_hall"},
    ]
    edges, ids = build_edge_configs(raw)
    assert edges[0].target_kind == TARGET_OUTSIDE
    assert edges[0].open_policy == "trip_off"
    assert edges[1].target_kind == TARGET_SENSOR
    assert edges[1].neighbor_temp_sensor == "sensor.hall_temp"
    assert ids == {"binary_sensor.window_1", "binary_sensor.door_hall"}


def test_outside_and_sensor_edges_resolve():
    hass = _FakeHass()
    hass.states.set("binary_sensor.window_1", "on")     # open
    hass.states.set("binary_sensor.door_hall", "on")    # open
    hass.states.set("sensor.hall_temp", "19.5")
    coord = RoomCouplingCoordinator(hass)
    edges = [
        EdgeConfig(target_kind=TARGET_OUTSIDE,
                   aperture_entity_id="binary_sensor.window_1",
                   aperture_type="window"),
        EdgeConfig(target_kind=TARGET_SENSOR,
                   aperture_entity_id="binary_sensor.door_hall",
                   neighbor_temp_sensor="sensor.hall_temp"),
    ]
    coord.register_room("A", edges)
    resolved = {e.edge_id: e for e in coord.open_edges("A")}
    assert resolved["binary_sensor.window_1"].target_kind == TARGET_OUTSIDE
    assert resolved["binary_sensor.window_1"].neighbor_temp is None
    assert resolved["binary_sensor.door_hall"].neighbor_temp == 19.5
    assert coord.any_open("A") is True


def test_controlled_edge_exposes_neighbor_k_for_consensus():
    hass = _FakeHass()
    hass.states.set("binary_sensor.door_ab", "on")
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B",
                                         aperture_entity_id="binary_sensor.door_ab")])
    coord.register_room("B", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="A",
                                         aperture_entity_id="binary_sensor.door_ab")])
    coord.publish("B", {"t_int": 21.0, "available": True, "power_w": 50.0,
                        "coupling_k_by_neighbor": {"A": {"k": 0.07, "reliable": True}}})
    coord.publish("A", {"t_int": 20.0, "available": True})
    edge = coord.open_edges("A")[0]
    assert edge.neighbor_uid == "B"
    assert edge.neighbor_temp == 21.0
    assert edge.neighbor_k == 0.07
    assert edge.neighbor_reliable is True


def test_closed_aperture_not_returned():
    hass = _FakeHass()
    hass.states.set("binary_sensor.window_1", "off")
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_OUTSIDE,
                                         aperture_entity_id="binary_sensor.window_1")])
    assert coord.open_edges("A") == []
    assert coord.any_open("A") is False


def test_component_power_excludes_typed_nodes():
    hass = _FakeHass()
    hass.states.set("binary_sensor.window_1", "on")
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_OUTSIDE,
                                         aperture_entity_id="binary_sensor.window_1")])
    coord.publish("A", {"t_int": 20.0, "available": True, "power_w": 800.0})
    # Outside is not a registered powered node -> only A's own power counts.
    assert coord.component_power_w("A") == 800.0


def test_open_apertures_ignores_resolvability():
    """A physically-open aperture is reported even when the neighbour is
    unresolvable (fail-safe gate), while open_edges still excludes it."""
    hass = _FakeHass()
    hass.states.set("binary_sensor.door", "on")
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B",
                                         aperture_entity_id="binary_sensor.door")])
    # B never registered/published -> not resolvable for the fold.
    aps = coord.open_apertures("A")
    assert [a.edge_id for a in aps] == ["B"]
    assert coord.open_edges("A") == []


def test_room_edge_preserves_window_and_trip_off_policy():
    """A controlled<->controlled door declared window/trip_off keeps those
    fields through both open_apertures and open_edges (reconciled across sides)."""
    hass = _FakeHass()
    hass.states.set("binary_sensor.door", "on")
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B",
                                         aperture_entity_id="binary_sensor.door",
                                         aperture_type="window", open_policy="trip_off")])
    coord.register_room("B", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="A",
                                         aperture_entity_id="binary_sensor.door")])
    coord.publish("B", {"t_int": 20.0, "available": True})
    ap = coord.open_apertures("A")[0]
    assert ap.aperture_type == "window" and ap.open_policy == "trip_off"
    e = coord.open_edges("A")[0]
    assert e.aperture_type == "window" and e.open_policy == "trip_off"


def test_non_finite_neighbor_temp_treated_as_unavailable():
    """A neighbour temp sensor reading nan/inf must be rejected (treated as
    unavailable) so the non-finite value never propagates into the fold."""
    hass = _FakeHass()
    hass.states.set("binary_sensor.door_hall", "on")
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_SENSOR,
                                         aperture_entity_id="binary_sensor.door_hall",
                                         neighbor_temp_sensor="sensor.hall_temp")])
    hass.states.set("sensor.hall_temp", "nan")
    assert coord.open_edges("A") == []
    hass.states.set("sensor.hall_temp", "inf")
    assert coord.open_edges("A") == []
    hass.states.set("sensor.hall_temp", "-inf")
    assert coord.open_edges("A") == []
    # A finite value resolves normally.
    hass.states.set("sensor.hall_temp", "19.5")
    assert coord.open_edges("A")[0].neighbor_temp == 19.5


def test_read_temp_rejects_non_finite():
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    hass.states.set("sensor.t", "nan")
    assert coord._read_temp("sensor.t") is None
    hass.states.set("sensor.t", "inf")
    assert coord._read_temp("sensor.t") is None
    hass.states.set("sensor.t", "20.5")
    assert coord._read_temp("sensor.t") == 20.5


def test_edge_open_if_any_declared_sensor_open():
    """When both rooms declare the same controlled link with DIFFERENT aperture
    sensors, the doorway is open if ANY declared sensor is on — the other side's
    sensor must not be ignored (fail-safe)."""
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B",
                                         aperture_entity_id="binary_sensor.door_a")])
    coord.register_room("B", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="A",
                                         aperture_entity_id="binary_sensor.door_b")])
    coord.publish("A", {"t_int": 20.0, "available": True, "power_w": 100.0})
    coord.publish("B", {"t_int": 22.0, "available": True, "power_w": 50.0})
    # A's own declared sensor is off, but B declared a DIFFERENT sensor that is on.
    hass.states.set("binary_sensor.door_a", "off")
    hass.states.set("binary_sensor.door_b", "on")
    assert coord.any_open("A") is True
    assert [a.edge_id for a in coord.open_apertures("A")] == ["B"]
    assert len(coord.open_edges("A")) == 1
    # Power BFS must also see the doorway open.
    assert coord.component_power_w("A") == 150.0


def test_edge_closed_only_when_all_declared_sensors_closed():
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B",
                                         aperture_entity_id="binary_sensor.door_a")])
    coord.register_room("B", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="A",
                                         aperture_entity_id="binary_sensor.door_b")])
    coord.publish("A", {"t_int": 20.0, "available": True})
    coord.publish("B", {"t_int": 22.0, "available": True})
    hass.states.set("binary_sensor.door_a", "off")
    hass.states.set("binary_sensor.door_b", "off")
    assert coord.any_open("A") is False
    assert coord.open_edges("A") == []


def test_edge_ids_for_includes_passive_side_link():
    """A one-sided room link (only A declares it) must still be reported as an
    incident edge for the passive room B, so the handler can keep B's learned
    coefficient (keyed by A) across restarts."""
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B",
                                         aperture_entity_id="binary_sensor.door")])
    coord.register_room("B", [])  # B declares nothing
    assert coord.edge_ids_for("A") == {"B"}
    assert coord.edge_ids_for("B") == {"A"}


def test_edge_ids_for_includes_typed_edges():
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [
        EdgeConfig(target_kind=TARGET_OUTSIDE,
                   aperture_entity_id="binary_sensor.window_1"),
        EdgeConfig(target_kind=TARGET_SENSOR,
                   aperture_entity_id="binary_sensor.door_hall",
                   neighbor_temp_sensor="sensor.hall_temp"),
    ])
    assert coord.edge_ids_for("A") == {"binary_sensor.window_1", "binary_sensor.door_hall"}


def test_passive_side_coefficient_survives_prune_keepset():
    """End-to-end of the handler prune fix: the passive room's learned
    coefficient survives when the keep-set unions local ids with the
    coordinator's incident edge ids."""
    from custom_components.vtherm_smartpi.smartpi.coupling_estimator import (
        CouplingEstimator,
    )
    hass = _FakeHass()
    coord = RoomCouplingCoordinator(hass)
    coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B",
                                         aperture_entity_id="binary_sensor.door")])
    coord.register_room("B", [])  # one-sided: B declares nothing
    est = CouplingEstimator("B")
    est.load_state({"edges": {"A": {"k": 0.05, "n_ok": 10}}})
    assert est.coeff("A") > 0.0
    local_ids_for_b: set[str] = set()  # B has no local declarations
    # OLD behaviour (prune against local ids only) would drop "A":
    keep = set(local_ids_for_b) | coord.edge_ids_for("B")
    est.prune(keep)
    assert est.coeff("A") > 0.0  # passive-side coefficient preserved


def test_build_edge_configs_dedupes_neighbor_and_aperture():
    """A reused neighbour edge_id or a reused aperture sensor is dropped so the
    edges list stays in sync with the id set."""
    raw = [
        {CONF_CONN_NEIGHBOR_VTHERM: "B", CONF_CONN_DOOR_SENSOR: "binary_sensor.door"},
        {CONF_CONN_NEIGHBOR_VTHERM: "B", CONF_CONN_DOOR_SENSOR: "binary_sensor.door2"},
        {CONF_CONN_TARGET_KIND: TARGET_OUTSIDE,
         CONF_CONN_APERTURE_SENSOR: "binary_sensor.door"},
        {CONF_CONN_TARGET_KIND: TARGET_OUTSIDE,
         CONF_CONN_APERTURE_SENSOR: "binary_sensor.win"},
    ]
    edges, ids = build_edge_configs(raw)
    assert len(edges) == 2
    assert ids == {"B", "binary_sensor.win"}
