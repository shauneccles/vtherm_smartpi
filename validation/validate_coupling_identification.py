"""Validate SmartPI's coupling IDENTIFICATION against independent physics.

The simulator's SimpleThermalModel is  C·dT/dt = Q − K·(T−T_ext) — the exact
1R1C form SmartPI learns — so we can couple rooms with a KNOWN physical
conductance and check that SmartPI's real CouplingEstimator recovers it.

Ground-truth mapping (SmartPI units are per-minute, per the room's own C):
    a_true = P/C·60      b_true = K/C·60      k_ij = G_ij/C_i·60   (ASYMMETRIC)
    kappa  = 60·G_w/C    (open-window buoyancy sqrt-law coefficient)

Run:  .venv/bin/python validation/validate_coupling_identification.py
"""
import math

from sim_bootstrap import setup
setup()  # puts repo root + the (runtime-fetched) simulator on sys.path

from thermal_model import SimpleThermalModel
from custom_components.vtherm_smartpi.smartpi.coupling_estimator import CouplingEstimator
from custom_components.vtherm_smartpi.smartpi.room_coupling import (
    ResolvedEdge, TARGET_ROOM, TARGET_OUTSIDE, compute_effective_params,
)

DT_SIM = 10.0       # plant integration step (s)
DT_CTRL = 300.0     # control / learning interval (s) -> dt_min = 5
TEXT = 5.0


class Room:
    def __init__(self, name, C, K=50.0, P=2000.0, target=21.0, t0=18.0):
        self.name, self.C, self.target = name, C, target
        self.plant = SimpleThermalModel(P, K, C, initial_temp=t0, initial_external_temp=TEXT)
        self.est = CouplingEstimator(name)
        self.a_true, self.b_true = P / C * 60.0, K / C * 60.0
        self.u = 0.0

    def control(self):
        # Proportional duty controller; rooms sit at different temps so doorway
        # gradients exist for the estimator to learn from.
        self.u = max(0.0, min(1.0, 1.5 * (self.target - self.plant.temperature)))
        self.plant.set_power_fraction(self.u)


def k_true(G, C):
    return G / C * 60.0


def _room_edge(uid, t_j):
    return ResolvedEdge(edge_id=uid, target_kind=TARGET_ROOM, aperture_type="door",
                        open_policy="model", neighbor_temp=t_j, neighbor_power_w=None,
                        neighbor_uid=uid)


def scenario1(G_door=100.0):
    A = Room("A", C=400_000, target=21.0, t0=20.0)   # living room (warm)
    B = Room("B", C=250_000, target=18.0, t0=17.0)   # bedroom (cooler, smaller)
    t = 0.0
    for _ in range(int(20 * 24 * 3600 / DT_CTRL)):   # 20 days
        A.control(); B.control()
        door = 8 <= (t / 3600.0) % 24.0 < 16          # open 8h/day
        for _ in range(int(DT_CTRL / DT_SIM)):
            q = G_door * (A.plant.temperature - B.plant.temperature) if door else 0.0
            A.plant.internal_gain_watts, B.plant.internal_gain_watts = -q, +q
            A.plant.step(DT_SIM); B.plant.step(DT_SIM)
        t += DT_CTRL
        ta, tb = A.plant.temperature, B.plant.temperature
        A.est.update(dt_min=DT_CTRL/60, tin=ta, text=TEXT, u=A.u, a=A.a_true, b=A.b_true,
                     open_edges=[_room_edge("B", tb)] if door else [], allow_learn=True)
        B.est.update(dt_min=DT_CTRL/60, tin=tb, text=TEXT, u=B.u, a=B.a_true, b=B.b_true,
                     open_edges=[_room_edge("A", ta)] if door else [], allow_learn=True)
    kA, kB = A.est.coeff("B"), B.est.coeff("A")
    kA_t, kB_t = k_true(G_door, A.C), k_true(G_door, B.C)
    print("\n=== Scenario 1: two coupled rooms (capacity-ratio asymmetry) ===")
    print(f"  ground truth : k_AB={kA_t:.4f}  k_BA={kB_t:.4f} /min   (C_A/C_B = {A.C/B.C:.2f})")
    print(f"  learned      : k_AB={kA:.4f} ({100*kA/kA_t:.0f}%)  k_BA={kB:.4f} ({100*kB/kB_t:.0f}%)")
    print(f"  asymmetry    : learned k_BA/k_AB={kB/kA:.2f}  vs truth {kB_t/kA_t:.2f}  "
          f"-> {'RECOVERED' if abs((kB/kA)/(kB_t/kA_t)-1) < 0.15 else 'MISSED'}")


def scenario2(door_pattern, label, G1=100.0, G2=80.0):
    A = Room("A", C=400_000, target=21.0, t0=20.0)
    B = Room("B", C=250_000, target=18.0, t0=17.0)
    C = Room("C", C=300_000, target=16.0, t0=16.0)
    t = 0.0
    for _ in range(int(25 * 24 * 3600 / DT_CTRL)):   # 25 days
        A.control(); B.control(); C.control()
        o1, o2 = door_pattern(t)
        for _ in range(int(DT_CTRL / DT_SIM)):
            q1 = G1 * (A.plant.temperature - B.plant.temperature) if o1 else 0.0
            q2 = G2 * (A.plant.temperature - C.plant.temperature) if o2 else 0.0
            A.plant.internal_gain_watts = -(q1 + q2)
            B.plant.internal_gain_watts, C.plant.internal_gain_watts = +q1, +q2
            A.plant.step(DT_SIM); B.plant.step(DT_SIM); C.plant.step(DT_SIM)
        t += DT_CTRL
        ta, tb, tc = A.plant.temperature, B.plant.temperature, C.plant.temperature
        edges = ([_room_edge("B", tb)] if o1 else []) + ([_room_edge("C", tc)] if o2 else [])
        A.est.update(dt_min=DT_CTRL/60, tin=ta, text=TEXT, u=A.u, a=A.a_true, b=A.b_true,
                     open_edges=edges, allow_learn=True)
    kB, kC = A.est.coeff("B"), A.est.coeff("C")
    kB_t, kC_t = k_true(G1, A.C), k_true(G2, A.C)
    print(f"\n=== Scenario 2: multi-edge (A coupled to B and C) — {label} ===")
    print(f"  ground truth : k_AB={kB_t:.4f}  k_AC={kC_t:.4f} /min")
    print(f"  learned      : k_AB={kB:.4f} ({100*kB/kB_t:.0f}%, reliable={A.est.reliable('B')})  "
          f"k_AC={kC:.4f} ({100*kC/kC_t:.0f}%, reliable={A.est.reliable('C')})")


def scenario3(Gw=10.0):
    A = Room("A", C=400_000, target=21.0, t0=20.0)
    t = 0.0
    for _ in range(int(20 * 24 * 3600 / DT_CTRL)):
        A.control()
        win = 7 <= (t / 3600.0) % 24.0 < 12
        for _ in range(int(DT_CTRL / DT_SIM)):
            d = A.plant.temperature - TEXT
            q = Gw * math.copysign(abs(d) ** 1.5, d) if win else 0.0   # buoyancy sqrt-law
            A.plant.internal_gain_watts = -q
            A.plant.step(DT_SIM)
        t += DT_CTRL
        ta = A.plant.temperature
        edge = ResolvedEdge(edge_id="win", target_kind=TARGET_OUTSIDE, aperture_type="window",
                            open_policy="model", neighbor_temp=None, neighbor_power_w=None)
        A.est.update(dt_min=DT_CTRL/60, tin=ta, text=TEXT, u=A.u, a=A.a_true, b=A.b_true,
                     open_edges=[edge] if win else [], allow_learn=True)
    kappa, kappa_t = A.est.coeff("win"), 60.0 * Gw / A.C
    dT = 16.0
    k_inst = A.est.k("win", TEXT + dT, TEXT, TARGET_OUTSIDE)
    b_eff, t_eff = compute_effective_params(A.b_true, TEXT, k_inst, k_inst * TEXT)
    print("\n=== Scenario 3: outside window (buoyancy sqrt-law) ===")
    print(f"  ground truth : kappa={kappa_t:.5f}  (min^-1·°C^-0.5)")
    print(f"  learned      : kappa={kappa:.5f} ({100*kappa/kappa_t:.0f}%, reliable={A.est.reliable('win')})")
    print(f"  fold @ΔT={dT:.0f} : b_base={A.b_true:.4f} -> b_eff={b_eff:.4f} (~{b_eff/A.b_true:.1f}x loss), "
          f"T_eff={t_eff:.2f} (=T_ext? {'YES' if abs(t_eff - TEXT) < 1e-6 else 'NO'})")


def _varied(t):
    h = (t / 3600.0) % 24.0
    if 6 <= h < 10:  return (True, False)
    if 10 <= h < 14: return (True, True)     # BOTH open (old code held learning here)
    if 14 <= h < 18: return (False, True)
    return (False, False)


def _always_together(t):
    h = (t / 3600.0) % 24.0
    return (8 <= h < 16, 8 <= h < 16)         # collinear -> individually unidentifiable


if __name__ == "__main__":
    print("SmartPI coupling identification vs caiusseverus/heating-simulator (ground truth)")
    scenario1()
    scenario2(_varied, "varied solo+joint patterns -> separable")
    scenario2(_always_together, "ALWAYS open together -> near-collinear (split not identifiable)")
    scenario3()
