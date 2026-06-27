"""Closed-loop validation: REAL SmartPI controllers drive a heater in each of two
rooms, coupled by a door. Shows the HEATER COMMANDS change at door open/close
once SmartPI has learned the coupling — contrasted with a coupling-blind group.

Notes on faithfulness:
  • The full SmartPI control law runs (PI + feed-forward + gain scheduling + the
    coupling fold). Only the multi-day base-model bootstrap is skipped by seeding
    each room's a/b into its STABLE regime, so the run focuses on the coupling
    behaviour (base identification is covered by the unit tests).
  • calculate() derives dt from time.monotonic(); we patch the algo module's clock
    to a controllable simulated time.

Run:  .venv/bin/python validation/validate_multiroom_closed_loop.py
"""
import statistics
from unittest.mock import MagicMock

from sim_bootstrap import setup
setup()

import custom_components.vtherm_smartpi.algo as algo_mod
from custom_components.vtherm_smartpi.algo import SmartPI
from custom_components.vtherm_smartpi.hvac_mode import VThermHvacMode_HEAT
from custom_components.vtherm_smartpi.smartpi.room_coupling import (
    RoomCouplingCoordinator, EdgeConfig, TARGET_ROOM,
)
from thermal_model import SimpleThermalModel


class _Clock:
    def __init__(self): self.t = 100000.0
    def monotonic(self): return self.t
    def time(self): return self.t

CLOCK = _Clock()
algo_mod.time = CLOCK   # algo.py does `import time` then time.monotonic(); redirect it

DT_CTRL, DT_SIM, TEXT = 300.0, 10.0, 5.0


class _State:
    def __init__(self, s): self.state = s
class _States:
    def __init__(self): self._d = {}
    def set(self, e, s): self._d[e] = _State(s)
    def get(self, e): return self._d.get(e)
class _Hass:
    def __init__(self): self.states = _States()


def _seed_stable(algo, a_true, b_true):
    """Seed a/b + reliability so the controller is in STABLE (post-bootstrap)."""
    e = algo.est; e.a, e.b = a_true, b_true
    for _ in range(25):
        e.a_meas_hist.append(a_true); e.b_meas_hist.append(b_true)
        e._a_hat_hist.append(a_true); e._b_hat_hist.append(b_true)
    e.learn_ok_count = 60; e.learn_ok_count_a = 30; e.learn_ok_count_b = 30
    algo.dt_est.deadtime_heat_s = 1.0; algo.dt_est.deadtime_heat_reliable = True
    algo.dt_est.deadtime_cool_s = 1.0; algo.dt_est.deadtime_cool_reliable = True
    algo._cycles_since_reset = 50; algo._startup_grace_period = False


class Room:
    def __init__(self, uid, C, target, t0, P=2000.0, K=50.0):
        self.uid, self.target, self.C = uid, target, C
        self.plant = SimpleThermalModel(P, K, C, initial_temp=t0, initial_external_temp=TEXT)
        self.algo = SmartPI(hass=MagicMock(), cycle_min=DT_CTRL / 60.0, minimal_activation_delay=0,
                            minimal_deactivation_delay=0, name=uid, use_ff3=False, debug_mode=False)
        _seed_stable(self.algo, P / C * 60.0, K / C * 60.0)
        self.prev_t, self.on = t0, 0.0

    def control(self):
        slope_h = (self.plant.temperature - self.prev_t) / (DT_CTRL / 3600.0)
        self.prev_t = self.plant.temperature
        self.algo.calculate(target_temp=self.target, current_temp=self.plant.temperature,
                            ext_current_temp=TEXT, hvac_mode=VThermHvacMode_HEAT,
                            slope=slope_h, cycle_boundary=True)
        self.on = self.algo.on_percent
        # Commit the computed output as the applied duty so the next cycle's
        # coupling residual uses the real u (on_cycle_started does this for real).
        self.algo._commit_current_linear_output()
        self.plant.set_power_fraction(self.on)


def _build(aware):
    hass = _Hass(); hass.states.set("binary_sensor.door_AB", "off")
    coord = RoomCouplingCoordinator(hass)
    A = Room("A", C=400_000, target=21.0, t0=21.0)   # living room (warm)
    B = Room("B", C=250_000, target=18.0, t0=18.0)   # bedroom (cooler, smaller)
    if aware:
        vA = coord.register_room("A", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="B",
                                                  aperture_entity_id="binary_sensor.door_AB")])
        vB = coord.register_room("B", [EdgeConfig(target_kind=TARGET_ROOM, neighbor_uid="A",
                                                  aperture_entity_id="binary_sensor.door_AB")])
        A.algo.attach_coupling_view(vA); B.algo.attach_coupling_view(vB)
    return hass, A, B


def _step_plants(A, B, door, G=120.0):
    for _ in range(int(DT_CTRL / DT_SIM)):
        q = G * (A.plant.temperature - B.plant.temperature) if door else 0.0
        A.plant.internal_gain_watts, B.plant.internal_gain_watts = -q, +q
        A.plant.step(DT_SIM); B.plant.step(DT_SIM)


def run(aware, days_learn=15):
    hass, A, B = _build(aware)
    t = 0.0
    for _ in range(int(days_learn * 24 * 3600 / DT_CTRL)):     # learning: door open 8h/day
        CLOCK.t += DT_CTRL
        door = 8 <= (t / 3600.0) % 24.0 < 16
        hass.states.set("binary_sensor.door_AB", "on" if door else "off")
        A.control(); B.control(); _step_plants(A, B, door)
        t += DT_CTRL
    learned = A.algo.coupling_est.coeff("B") if aware else 0.0
    log, t0 = [], t
    for _ in range(int(8 * 3600 / DT_CTRL)):                   # demo: shut/OPEN(2-5h)/shut
        CLOCK.t += DT_CTRL
        rel_h = (t - t0) / 3600.0
        door = 2.0 <= rel_h < 5.0
        hass.states.set("binary_sensor.door_AB", "on" if door else "off")
        A.control(); B.control(); _step_plants(A, B, door)
        log.append((rel_h, door, A.plant.temperature, A.on, B.on,
                    getattr(A.algo, "_coupling_b_eff", None)))
        t += DT_CTRL
    return learned, log


if __name__ == "__main__":
    print("=== Multi-room closed loop: real SmartPI heaters, coupled by a door ===")
    print("    Living room A (target 21°C) <-door-> Bedroom B (target 18°C), G=120 W/°C")
    learned, aw = run(aware=True)
    _, bl = run(aware=False)
    kt = 120 / 400_000 * 60
    print(f"\n  After learning, SmartPI(A) learned k_AB={learned:.4f}/min "
          f"(truth {kt:.4f}, {100 * learned / kt:.0f}%)\n")
    print("       MODEL-AWARE (learned coupling)        | COUPLING-BLIND")
    print("  hour door | heaterA heaterB  T_A   b_eff   | heaterA  T_A")
    for rel_h, door, TA, onA, onB, beff in aw:
        i = int(rel_h * 3600 / DT_CTRL)
        if int(rel_h * 3600 / DT_CTRL) % 3 == 0:               # every 15 min
            d = "OPEN " if door else "shut "
            print(f"  {rel_h:4.1f} {d}|  {onA*100:4.0f}%   {onB*100:4.0f}%  {TA:6.2f}  {beff:.4f} | "
                  f"  {bl[i][3]*100:4.0f}%  {bl[i][2]:6.2f}")
    aw_open = [r for r in aw if r[1]]
    aw_shut = [r for r in aw if not r[1] and r[0] < 2]
    bl_open = [bl[i] for i, r in enumerate(aw) if r[1]]
    m = lambda rows, idx: statistics.mean([r[idx] * 100 for r in rows])
    print("\n  When the door OPENS (model now understands heat flows A->B):")
    print(f"    heater A (warm): {m(aw_shut,3):.0f}% -> {m(aw_open,3):.0f}%  (ramps UP, anticipates loss via b_eff fold)")
    print(f"    heater B (cool): {m(aw_shut,4):.0f}% -> {m(aw_open,4):.0f}%  (eases OFF, gets free heat from A)")
    print(f"    b_eff(A): 0.0075 -> {statistics.mean([r[5] for r in aw_open]):.4f}  (= b + learned k, door-driven)")
    off_aw = statistics.mean([abs(r[2] - 21.0) for r in aw_open])
    off_bl = statistics.mean([abs(r[2] - 21.0) for r in bl_open])
    print(f"    mean |T_A - target| during open window:  AWARE {off_aw:.2f}°C   BLIND {off_bl:.2f}°C")
