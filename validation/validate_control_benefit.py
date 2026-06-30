"""Validate the CONTROL benefit of the coupling fold (feed-forward isolation).

Same plant (room A with a door to a much colder room that opens for 2h), two
identical PI controllers. The only difference: the coupling-AWARE controller
folds the (learned) open-door coupling into its feed-forward via SmartPI's real
compute_effective_params; the BLIND controller assumes the room is uncoupled.

Run:  .venv/bin/python validation/validate_control_benefit.py
"""
import math

from sim_bootstrap import setup
setup()

from thermal_model import SimpleThermalModel
from custom_components.vtherm_smartpi.smartpi.room_coupling import compute_effective_params

DT_SIM, DT_CTRL, TEXT = 10.0, 60.0, 5.0
C_A, K_A, P_A = 400_000.0, 50.0, 2000.0
C_B, G_DOOR, TARGET = 250_000.0, 120.0, 21.0
a = P_A / C_A * 60.0           # °C/min/duty
b = K_A / C_A * 60.0           # /min envelope
k_door = G_DOOR / C_A * 60.0   # /min — A's coupling coefficient (as the model learns it)


def simulate(aware):
    A = SimpleThermalModel(P_A, K_A, C_A, initial_temp=TARGET, initial_external_temp=TEXT)
    B = SimpleThermalModel(2000, 50, C_B, initial_temp=14.0, initial_external_temp=TEXT)
    integral, t, devs = 0.0, 0.0, []
    Kp, Ki = 0.25, 0.0010
    for _ in range(int(6 * 3600 / DT_CTRL)):          # 6 hours
        door = 2.0 * 3600 <= t < 4.0 * 3600           # door open hours 2-4
        Ta, Tb = A.temperature, B.temperature
        if aware and door:
            k_learned = 0.97 * k_door                 # value SmartPI actually learns
            b_eff, t_eff = compute_effective_params(b, TEXT, k_learned, k_learned * Tb)
            u_ff = b_eff * (TARGET - t_eff) / a
        else:
            u_ff = b * (TARGET - TEXT) / a
        err = TARGET - Ta
        integral = max(-200.0, min(200.0, integral + err * (DT_CTRL / 60.0)))
        u = max(0.0, min(1.0, u_ff + Kp * err + Ki * integral))
        A.set_power_fraction(u)
        B.set_power_fraction(max(0.0, min(1.0, 1.0 * (14.0 - Tb))))   # hold B ~14
        for _ in range(int(DT_CTRL / DT_SIM)):
            q = G_DOOR * (A.temperature - B.temperature) if door else 0.0
            A.internal_gain_watts, B.internal_gain_watts = -q, +q
            A.step(DT_SIM); B.step(DT_SIM)
        t += DT_CTRL
        if t >= 2.0 * 3600:
            devs.append(A.temperature - TARGET)
    rms = math.sqrt(sum(d * d for d in devs) / len(devs))
    return min(devs), rms


if __name__ == "__main__":
    print("=== Control benefit: a door to a cold (14°C) room opens for 2h ===")
    print(f"  room A target {TARGET}°C; open door gives k={k_door:.4f}/min (~{1 + k_door / b:.1f}x loss)\n")
    for aware in (False, True):
        worst, rms = simulate(aware)
        tag = "coupling-AWARE" if aware else "coupling-BLIND"
        print(f"  {tag:15s}: worst dip {worst:+.2f}°C, RMS deviation {rms:.3f}°C")
