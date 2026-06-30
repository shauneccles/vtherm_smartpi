# SmartPI room-coupling validation

These scripts validate the room-coupling network model by driving the **real**
SmartPI coupling code against an independent third-party thermal-physics engine,
[`caiusseverus/heating-simulator`](https://github.com/caiusseverus/heating-simulator),
used as ground truth. Its `SimpleThermalModel` is `C·dT/dt = Q − K·(T−T_ext)` —
the exact 1R1C form SmartPI learns — so rooms can be coupled with a *known*
physical conductance and we can check what SmartPI recovers.

## The third-party simulator (no vendoring)

The simulator has **no license file**, so its code is **not** copied into this
repository, and it is not published on PyPI. `sim_bootstrap.py` fetches it **at
runtime** into a cache *outside* the repo (a shallow `git clone` pinned to a known
commit). Nothing third-party is committed here. Please review the simulator's own
terms before use.

Source resolution order (`sim_bootstrap.simulator_path()`):

1. `$HEATING_SIMULATOR_PATH` — point at a local checkout to use it as-is.
2. A cached clone under `$XDG_CACHE_HOME` (or `~/.cache/vtherm-smartpi-validation/`).
3. A fresh shallow clone of the pinned commit (needs `git` + network once).

If you are offline with no cache, clone it manually and set the env var:

```bash
git clone https://github.com/caiusseverus/heating-simulator /some/path
export HEATING_SIMULATOR_PATH=/some/path
```

## Requirements

Importing `custom_components.vtherm_smartpi` pulls in Home Assistant, so run these
in the project's **test environment** (the same one `pytest` uses). See
`requirements_test.txt` / `.github/workflows/pytest.yml`. A quick `uv` setup:

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python pytest pytest-asyncio coverage \
    "homeassistant==2026.3.1" pytest-homeassistant-custom-component vtherm-api
```

## Run

From the repository root:

```bash
.venv/bin/python validation/validate_coupling_identification.py   # learns k vs ground truth
.venv/bin/python validation/validate_control_benefit.py           # feed-forward benefit
.venv/bin/python validation/validate_multiroom_closed_loop.py     # real controllers, heaters vs doors
```

## What each script shows

| Script | Validates |
|---|---|
| `validate_coupling_identification.py` | SmartPI's `CouplingEstimator` recovers the true inter-room conductance (~97%) and the **capacity-ratio asymmetry** `k_ij = G/C_i ≠ k_ji`; learns **multiple simultaneously-open** apertures (the upgrade over the old single-aperture estimator); recovers the open-window **√\|ΔT\| buoyancy** coefficient and folds it correctly (`T_eff = T_ext`). |
| `validate_control_benefit.py` | Folding the learned coupling into the feed-forward sharply reduces the temperature dip when a door to a cold room opens. |
| `validate_multiroom_closed_loop.py` | With the **full** SmartPI controllers in closed loop, the **heater commands change as doors open/close** once the coupling is learned: the warm room's heater ramps up, the cool room's eases off, both reverting when the door shuts — vs a coupling-blind group that only reacts and holds a steady offset. |
| `validate_my_house.py` | A real (anonymized) multi-room layout discovered from a live Home Assistant instance: three heated rooms coupled **through buffer rooms** (a multi-hop chain) plus per-room windows to outside. Confirms SmartPI recovers each door/window conductance on a genuine topology, and surfaces the near-collinear limit where a mid-chain room's doors are usually open together. |

## Honest caveats

- The closed-loop script seeds each controller's base `a/b` model into its STABLE
  regime to skip the multi-day bootstrap and focus on the coupling behaviour; base
  identification is covered by the unit tests.
- Two apertures that are **always** open together (to rooms at different
  temperatures) are near-collinear and not individually separable — the per-edge
  split is unreliable there (the aggregate loss the controller uses stays correct).
  A sharper conditioning gate (condition-number / Fisher, not just variance) would
  flag this; it's noted as a follow-up.
