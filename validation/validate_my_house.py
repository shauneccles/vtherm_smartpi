"""Validate SmartPI on a real multi-room house topology.

This mirrors an actual home layout discovered from a live Home Assistant
instance (areas + door/window binary_sensors), anonymized here: the structure,
the per-aperture conductances, the schedules, and the live initial
temps/setpoints are faithful; only the room names and entity IDs are generic.

    room_b ──door(no sensor)── hub ──door(c_hub)── room_c ──door(a_c)── room_a
   (heated)                 (buffer)             (heated, open-plan   (heated)
       │                                          + 2nd space) │
   window→out                                        door(c_util)│      window→out
                                                            util (buffer)
   room_c → outside: two external apertures (patio door + window)

Heated rooms (real SmartPI control): room_a, room_b, room_c.
Buffers (uncontrolled, sensed): hub, util.

The heated rooms couple THROUGH the buffers — a genuine multi-hop network. We
drive the real SmartPI controllers closed-loop against heating-simulator physics
with a KNOWN conductance per door/window, and check SmartPI recovers it.

Heat-loss K for the two switch-heated rooms is DATA-GROUNDED from ~14 days of
real HA history (steady-state energy balance K = P·mean(duty)/mean(T−T_out));
room_b measured ~4.6× leakier than room_a — it runs its heater >50% of the time
and still sits below its 22° setpoint. Thermal mass C is assumed (history can't
separate it), and the live home runs VTherm over_switch/TPI — which does NOT
learn a/b — so this drives the real SmartPI algorithm on the rooms' measured
heat loss.
room_b's door-to-hub and window have no contact sensor in the real home; they
are modelled with virtual sensors here (in reality a contact sensor or helper is
needed for SmartPI to gate those edges).

Run:  .venv/bin/python validation/validate_my_house.py
"""
import math
from unittest.mock import MagicMock

from sim_bootstrap import setup
setup()

import custom_components.vtherm_smartpi.algo as algo_mod
from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.room_coupling import (
    RoomCouplingCoordinator, EdgeConfig, TARGET_ROOM, TARGET_SENSOR, TARGET_OUTSIDE,
)
from thermal_model import SimpleThermalModel


class _Clock:
    def __init__(self): self.t = 100000.0
    def monotonic(self): return self.t
    def time(self): return self.t
CLOCK = _Clock(); algo_mod.time = CLOCK

DT_CTRL, DT_SIM, TEXT = 300.0, 10.0, 11.0   # observed mean outdoor over the fit window

# --- house topology -----------------------------------------------------------
# K (heat-loss W/°C) for the two switch-heated rooms is DATA-GROUNDED: derived
# from ~14 days of real HA history via the steady-state energy balance
# K = P*mean(duty)/mean(T - T_out)  (the robust signal; a naive dT/dt fit is
# swamped by household noise). Observed: room_a ran the heater ~14% of the time
# at K≈32; room_b ran it ~54% and STILL sat below its 22° target -> K≈148,
# ~4.6x leakier. room_c (AC-controlled, no switch history) keeps an assumed K.
# C (thermal mass) is assumed throughout (history can't separate it).
# t0 = live room temps; targets = live setpoints.
ROOMS = {
    "room_a": dict(C=350_000, K=32,  P=2000, target=17.0, t0=19.6, heated=True),
    "room_b": dict(C=400_000, K=148, P=2000, target=22.0, t0=14.5, heated=True),
    "room_c": dict(C=700_000, K=90,  P=3000, target=21.0, t0=19.2, heated=True),
    "hub":    dict(C=200_000, K=20,  heated=False, t0=16.0),
    "util":   dict(C=150_000, K=25,  heated=False, t0=14.0),
}
# interior doors: (roomA, roomB, conductance W/°C, aperture sensor entity | None)
DOORS = [
    ("room_a", "room_c", 100.0, "binary_sensor.door_a_c"),
    ("room_c", "hub",    110.0, "binary_sensor.door_c_hub"),
    ("room_b", "hub",     90.0, None),     # no sensor -> virtual
    ("room_c", "util",    70.0, "binary_sensor.door_c_util"),
]
# windows / external doors to OUTSIDE: (room, conductance, aperture sensor | None)
WINDOWS = [
    ("room_a", 8.0,  "binary_sensor.window_a"),
    ("room_b", 10.0, None),                # no sensor -> virtual
    ("room_c", 14.0, "binary_sensor.window_c1"),   # external patio door
    ("room_c", 8.0,  "binary_sensor.window_c2"),   # secondary-space window
]
# virtual sensors for room_b's sensorless apertures
ROOM_B_DOOR = "binary_sensor.door_b_hub_virtual"
ROOM_B_WIN = "binary_sensor.window_b_virtual"


class _State:
    def __init__(self, s): self.state = s
class _States:
    def __init__(self): self._d = {}
    def set(self, e, s): self._d[e] = _State(str(s))
    def get(self, e): return self._d.get(e)
class _Hass:
    def __init__(self): self.states = _States()


def _seed_stable(algo, a_true, b_true):
    e = algo.est; e.a, e.b = a_true, b_true
    for _ in range(25):
        e.a_meas_hist.append(a_true); e.b_meas_hist.append(b_true)
        e._a_hat_hist.append(a_true); e._b_hat_hist.append(b_true)
    e.learn_ok_count = 60; e.learn_ok_count_a = 30; e.learn_ok_count_b = 30
    algo.dt_est.deadtime_heat_s = 1.0; algo.dt_est.deadtime_heat_reliable = True
    algo.dt_est.deadtime_cool_s = 1.0; algo.dt_est.deadtime_cool_reliable = True
    algo._cycles_since_reset = 50; algo._startup_grace_period = False


def _door_sensor(a, b, s):
    return s if s is not None else ROOM_B_DOOR     # the only sensorless door is room_b<->hub


def build():
    hass = _Hass()
    coord = RoomCouplingCoordinator(hass)
    plants, algos, prev = {}, {}, {}
    for name, r in ROOMS.items():
        plants[name] = SimpleThermalModel(r.get("P", 0) or 2000, r["K"], r["C"],
                                          initial_temp=r["t0"], initial_external_temp=TEXT)
        prev[name] = r["t0"]
    for name, r in ROOMS.items():
        if not r["heated"]:
            continue
        a = SmartPI(hass=MagicMock(), cycle_min=DT_CTRL/60.0, minimal_activation_delay=0,
                    minimal_deactivation_delay=0, name=name, use_ff3=False, debug_mode=False)
        _seed_stable(a, (r["P"]/r["C"])*60.0, (r["K"]/r["C"])*60.0)
        algos[name] = a
    edges = {n: [] for n in algos}
    for (x, y, G, sens) in DOORS:
        ap = _door_sensor(x, y, sens)
        for me, other in ((x, y), (y, x)):
            if me not in algos:
                continue
            if ROOMS[other]["heated"]:                     # controlled neighbour
                edges[me].append(EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid=other,
                                            aperture_entity_id=ap))
            else:                                          # sensed buffer
                edges[me].append(EdgeConfig(target_kind=TARGET_SENSOR,
                                            neighbor_temp_sensor=f"sensor.{other}_temp",
                                            aperture_entity_id=ap))
    for (room, Gw, sens) in WINDOWS:
        if room in algos:
            ap = sens if sens is not None else ROOM_B_WIN
            edges[room].append(EdgeConfig(target_kind=TARGET_OUTSIDE, aperture_entity_id=ap,
                                          aperture_type="window"))
    for name, a in algos.items():
        a.attach_coupling_view(coord.register_room(name, edges[name]))
    return hass, coord, plants, algos, prev


def open_states(t):
    """Daily aperture schedule for the learning phase."""
    h = (t / 3600.0) % 24.0
    return {
        "binary_sensor.door_a_c":    7 <= h < 23,
        "binary_sensor.door_c_hub":  6 <= h < 22,
        ROOM_B_DOOR:                 9 <= h < 13 or 16 <= h < 20,
        "binary_sensor.door_c_util": 10 <= h < 11 or 18 <= h < 19,
        "binary_sensor.window_a":    13 <= h < 15,
        ROOM_B_WIN:                  14 <= h < 16,
        "binary_sensor.window_c1":   12 <= h < 14,
        "binary_sensor.window_c2":   11 <= h < 12,
    }


def step(plants, opens):
    for _ in range(int(DT_CTRL / DT_SIM)):
        gains = {n: 0.0 for n in plants}
        for (x, y, G, sens) in DOORS:
            if opens[_door_sensor(x, y, sens)]:
                q = G * (plants[x].temperature - plants[y].temperature)
                gains[x] -= q; gains[y] += q
        for (room, Gw, sens) in WINDOWS:
            if opens[sens if sens is not None else ROOM_B_WIN]:
                d = plants[room].temperature - TEXT
                gains[room] -= Gw * math.copysign(abs(d) ** 1.5, d)
        for n, p in plants.items():
            p.internal_gain_watts = gains[n]
            p.step(DT_SIM)


def control(name, algo, plants, prev):
    slope_h = (plants[name].temperature - prev[name]) / (DT_CTRL / 3600.0)
    prev[name] = plants[name].temperature
    algo.calculate(target_temp=ROOMS[name]["target"], current_temp=plants[name].temperature,
                   ext_current_temp=TEXT, hvac_mode=VThermHvacMode_HEAT,
                   slope=slope_h, cycle_boundary=True)
    algo._commit_current_linear_output()
    plants[name].set_power_fraction(algo.on_percent)


def publish_sensors(hass, plants, opens):
    for n, r in ROOMS.items():
        if not r["heated"]:
            hass.states.set(f"sensor.{n}_temp", round(plants[n].temperature, 2))
    for ent, isopen in opens.items():
        hass.states.set(ent, "on" if isopen else "off")


def run(days=14):
    hass, coord, plants, algos, prev = build()
    t = 0.0
    for _ in range(int(days * 24 * 3600 / DT_CTRL)):
        CLOCK.t += DT_CTRL
        opens = open_states(t)
        publish_sensors(hass, plants, opens)
        for name in algos:
            control(name, algos[name], plants, prev)
        step(plants, opens)
        t += DT_CTRL
    return algos, plants


def gt_room_k(G, room):        return G / ROOMS[room]["C"] * 60.0
def gt_window_kappa(Gw, room): return 60.0 * Gw / ROOMS[room]["C"]


if __name__ == "__main__":
    print("=== SmartPI on a real (anonymized) multi-room house ===")
    print("    heated: room_a, room_b, room_c | buffers: hub, util")
    print("    chain: room_b—hub—room_c—room_a (+ room_c—util)\n")
    algos, plants = run()

    print("  Learned coupling vs known conductance (per heated room's edges):")
    print("  edge                              truth k/κ   learned    %")
    rows = [
        ("room_a", "room_c",                       "room",   gt_room_k(100, "room_a")),
        ("room_c", "room_a",                       "room",   gt_room_k(100, "room_c")),
        ("room_c", "binary_sensor.door_c_hub",     "sensor", gt_room_k(110, "room_c")),
        ("room_c", "binary_sensor.door_c_util",    "sensor", gt_room_k(70, "room_c")),
        ("room_b", ROOM_B_DOOR,                     "sensor", gt_room_k(90, "room_b")),
    ]
    for room, edge_id, kind, truth in rows:
        learned = algos[room].coupling_est.coeff(edge_id)
        rel = algos[room].coupling_est.reliable(edge_id)
        label = f"{room} -> {edge_id.split('.')[-1] if '.' in edge_id else edge_id}"
        print(f"  {label:33.33s} {truth:7.4f}   {learned:7.4f}  {100*learned/truth:4.0f}% {'ok' if rel else ''}")
    print("  windows (kappa, min^-1·°C^-0.5):")
    for room, ent, gw in (("room_a", "binary_sensor.window_a", 8.0),
                          ("room_b", ROOM_B_WIN, 10.0),
                          ("room_c", "binary_sensor.window_c1", 14.0)):
        truth = gt_window_kappa(gw, room)
        learned = algos[room].coupling_est.coeff(ent)
        print(f"  {room+' window':33.33s} {truth:7.5f}   {learned:7.5f}  {100*learned/truth:4.0f}%")

    print("\n  Final room temps (target):")
    for n, r in ROOMS.items():
        tag = f"target {r['target']:.0f}" if r["heated"] else "buffer"
        print(f"    {n:7s} {plants[n].temperature:5.2f}°C  ({tag})")
