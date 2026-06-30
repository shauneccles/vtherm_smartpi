# Room-Network Discovery (Guided Connection Mapping) — Design Spec

**Date:** 2026-06-30
**Branch:** `feat/room-coupling-model`
**Status:** Approved design, pending implementation plan
**Component:** `custom_components/vtherm_smartpi` (SmartPI adaptive PI controller for Versatile Thermostat)

---

## 1. Purpose

The room-coupling model (see `2026-06-27-room-coupling-network-model-design.md`) consumes a
per-entry list of **connections** (`smart_pi_connections`): each is an aperture (door/window
sensor) joining this room to a neighbour node (another controlled VTherm, a sensed area, or
outside). Today these are entered **by hand, one at a time**, in the options flow — tedious and
error-prone, and the user has to remember every door/window sensor's `entity_id`.

This feature adds **guided discovery**: when configuring a VTherm's SmartPI options, the flow
auto-scans Home Assistant for the door/window sensors that belong to *this room*, pre-fills each
one (sensor + aperture type), and presents a **single form** where the user picks the far
endpoint (and policy) per aperture. It **does not guess adjacency** — HA does not model which two
rooms an aperture joins, so the far endpoint is always the user's choice. The existing manual
entry path is kept as a permanent escape hatch.

The feature only **produces** the same `smart_pi_connections` shape the model already consumes;
the coupling model, estimator, fold, and persisted state are untouched.

## 2. Background — what HA gives us, and what it does not

Observed on the live instance (1 floor "Shauns Shelter", 11 areas):

- **Controlled rooms** = VTherm `climate` entities (`platform: versatile_thermostat`):
  `climate.bedroom_heating` (area `bedroom`), `climate.playroom_heating` (area `playroom`),
  `climate.living_area_ac` (**area `null`**).
- **Sensed rooms** = HA areas carry a `temperature_entity_id` (e.g. area `kitchen` →
  `sensor.kitchen_sensor_temperature`). This is the temperature source for a sensed neighbour.
- **Apertures** = `binary_sensor`s with `device_class ∈ {door, window, opening, garage_door}`,
  e.g. `binary_sensor.bedroom_door_contact` (area `bedroom`, class `door`),
  `binary_sensor.kitchen_window_contact` (class overridden to `window`).

Two realities constrain the design:

1. **Area assignment is registry-driven but patchy.** Some entities have no `entity.area_id`
   (`climate.living_area_ac`, `binary_sensor.living_area_glass_door_contact`,
   `binary_sensor.kitchen_window_contact`, `binary_sensor.garage_sliding_door_contact`). Per
   user decision, discovery relies **only on the registry area** — never on parsing names. An
   unassigned entity is simply not discovered; organizing areas in HA is the user's
   responsibility.
2. **HA has no adjacency.** It records that a door sensor is *in* the bedroom, never that it
   *joins* bedroom↔hallway. The far endpoint of every aperture is human knowledge absent from
   HA, so discovery pre-fills everything **except** the far endpoint, which the user picks.

## 3. Scope (decisions)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Deliverable | Guided discovery **in the config/options flow** (writes `smart_pi_connections`). |
| 2 | Adjacency | **No guessing.** Pre-fill aperture sensor + type + home room; user picks far endpoint. |
| 3 | Discovery scope | **This room's apertures only** — sensors whose home area = this VTherm's area. |
| 4 | UI shape | **Single form, all at once** — one row per discovered aperture. |
| 5 | Area resolution | **Registry only** — entity `area_id`, else the entity's *device* `area_id`. No name parsing. |
| 6 | Manual path | **Kept permanently** as an escape hatch (any `binary_sensor`, edit/remove). |

Out of scope: changing the coupling model/estimator/fold; whole-house topology view;
auto-writing across other config entries; adjacency heuristics; inferring apertures without a
sensor (a sensor is required, so an aperture cannot be mapped until its sensor exists).

## 4. Architecture

Three pieces, separated so the inference logic is testable without the HA config-flow machinery.

```
┌─────────────────────────────┐     ┌──────────────────────────────┐     ┌────────────────────┐
│ smartpi/topology.py         │     │ config_flow.py               │     │ smart_pi_connections│
│ (pure discovery, no writes) │ --> │ async_step_discover_         │ --> │ (per-entry list)   │
│                             │     │   connections (single form)  │     │ consumed by        │
│ resolve_room_area()         │     │ + async_step_init menu       │     │ build_edge_configs │
│ discover_room_apertures()   │     │ + existing manual loop kept  │     │ (unchanged)        │
│ discover_candidate_nodes()  │     │                              │     │                    │
└─────────────────────────────┘     └──────────────────────────────┘     └────────────────────┘
```

### 4.1 Discovery engine — `smartpi/topology.py` (new, pure)

No HA writes; reads the entity/device/area registries via `hass`. All functions return plain
dataclasses so they can be unit-tested against a fake registry.

```python
@dataclass(frozen=True)
class DiscoveredAperture:
    aperture_entity_id: str        # the binary_sensor
    name: str                      # friendly name for the form label
    aperture_type: str             # "window" if effective device_class == window else "door"
    current: dict | None           # existing smart_pi_connections entry for this sensor, or None

@dataclass(frozen=True)
class CandidateNodes:
    controlled: list[tuple[str, str]]   # (vtherm_unique_id, label) — SmartPI VTherms, excl. self
    sensed: list[tuple[str, str]]       # (area_temp_sensor_entity_id, label) — areas w/ temp sensor
    # "outside" is always offered (no payload)
```

- **`resolve_room_area(hass, vtherm_unique_id) -> str | None`** — the VTherm's area: entity
  `area_id`, else its device's `area_id`. `None` ⇒ nothing to discover (surface a clear message).
- **`discover_room_apertures(hass, area, existing) -> list[DiscoveredAperture]`** — every
  `binary_sensor` whose **effective** `device_class ∈ {door, window, opening, garage_door}`
  (registry override beats `original_device_class`) and whose **effective area** (entity, else
  device) equals `area`. **Excludes** VTherm-internal sensors (`platform: versatile_thermostat`,
  e.g. `*_window_state`) so the VTherm's own window detection is never offered as an aperture.
  `aperture_type = "window"` iff effective class is `window`, else `"door"`. `current` is the
  matching entry from the passed-in `existing` connections (idempotent re-runs).
- **`discover_candidate_nodes(hass, self_uid) -> CandidateNodes`** — far-endpoint choices:
  - **controlled**: VTherms where `target_uses_smartpi(...)` is true, excluding self → room edge.
  - **sensed**: every area with a `temperature_entity_id` → sensor edge (using that sensor).
  - **outside**: always available.

### 4.2 Config-flow integration — `config_flow.py`

The options flow is a **linear wizard** for per-thermostat entries:
`async_step_init` (main options) → `[async_step_valve_curve]` → `async_step_connections`
(the one-at-a-time loop) → `async_create_entry`. `_finish_or_connections` only routes to the
connections stage when the entry targets a VTherm (`CONF_TARGET_VTHERM`), so discovery is
naturally per-VTherm and never reached for the global/defaults entry.

- **New menu step `async_step_connections_menu`** (via `self.async_show_menu`), inserted where
  the wizard currently jumps straight to `async_step_connections`. `_finish_or_connections` is
  re-pointed at this menu for VTherm entries. Choices:
  - *"Discover room connections"* → `async_step_discover_connections` (new).
  - *"Edit connections manually"* → the existing `async_step_connections` loop (unchanged).
  - *"Done"* → `async_create_entry` with the current options/connections.
  The existing `async_step_connections` body is untouched; only its entry point moves behind the
  menu.
- **`async_step_discover_connections`**:
  1. Resolve this entry's VTherm area via `resolve_room_area`. If `None`, abort the step with a
     message telling the user to assign the VTherm to an area in HA.
  2. `discover_room_apertures` + `discover_candidate_nodes`.
  3. Build a **dynamic schema**, two fields per discovered aperture, keyed by its `entity_id`:
     - `<id>__endpoint`: `SelectSelector`, options `[Skip, Outside, <each controlled label>,
       <each sensed label>]`, default = the aperture's `current` wiring, else `Skip`.
     - `<id>__policy`: `SelectSelector` `[model, trip_off]`, default = `current` policy else `model`.
     The aperture friendly name is the field label; `aperture_type` shown read-only in the label
     (e.g. "Bedroom window (window)").
  4. On submit: for each row not `Skip`, translate the chosen endpoint into a connection dict and
     validate with the existing `validate_connection_entry`. **Merge idempotently** into
     `smart_pi_connections` — replace any existing entry with the same `aperture_sensor`, drop
     rows switched back to `Skip`, leave manually-added entries for sensors not in the discovered
     set untouched.

### 4.3 Far-endpoint → connection-dict mapping

| User picks | Connection dict written |
|------------|-------------------------|
| **Outside** | `{target_kind: outside, aperture_sensor, aperture_type, open_policy}` |
| **VTherm X** (controlled) | `{target_kind: room, neighbor_vtherm_entity: <X unique_id>, aperture_sensor, aperture_type, open_policy}` |
| **Sensed area Y** | `{target_kind: sensor, neighbor_temp_sensor: <Y temperature_entity_id>, aperture_sensor, aperture_type, open_policy}` |
| **Skip** | (omitted; existing entry for this sensor removed) |

These are exactly the keys `build_edge_configs` already parses — no model changes.

## 5. Components & responsibilities (isolation)

- `smartpi/topology.py` — *what:* turn the HA registries into discovered apertures + candidate
  nodes for one room. *Interface:* the three functions above, plain dataclasses in/out. *Depends
  on:* `hass` registries (read-only), `target_uses_smartpi`. No config-flow, no writes.
- `config_flow.py` additions — *what:* render the dynamic form, translate submissions into
  `smart_pi_connections`, merge idempotently. *Depends on:* `topology.py`, existing
  `validate_connection_entry`, existing selectors.
- Unchanged consumers — `build_edge_configs`, `RoomCouplingCoordinator`, estimator, fold.

## 6. Error handling & edge cases

- **VTherm has no area** → step aborts with an actionable message ("assign this thermostat to an
  area in Home Assistant, then re-run discovery"). Manual entry still works.
- **No apertures found** → step shows an empty-state message and a link back to manual entry.
- **Aperture already wired** → pre-selected to its current endpoint/policy; re-submitting is a
  no-op; switching to `Skip` removes it.
- **Far endpoint a sensed area with no temp sensor** → not offered (only areas with
  `temperature_entity_id` appear in `sensed`).
- **Sensor area changes / aperture deleted in HA** → next discovery reflects the new registry
  state; a previously-wired sensor that vanished remains in config until the user `Skip`s it (or
  removes it via manual edit) — discovery never silently deletes user config.
- **Duplicate aperture across rooms** — not possible here: scope is this room's area only, and a
  sensor belongs to one area; `build_edge_configs` also de-dupes by aperture sensor as a backstop.

## 7. Testing

Pure engine (`tests/test_topology.py`), against a fake entity/device/area registry:

- area resolution: entity `area_id`; device fallback; `None` when unassigned (no name parsing).
- aperture selection: includes door/window/opening/garage_door; honours device_class override;
  excludes `versatile_thermostat` platform sensors; excludes non-aperture classes
  (motion/tamper/battery).
- candidate nodes: controlled excludes self + non-SmartPI; sensed only areas with a temp sensor;
  outside always present.
- `current` back-reference matches an existing connection by aperture sensor.

Config flow (`tests/test_config_flow_discovery.py`):

- dynamic schema has one endpoint + one policy field per discovered aperture, defaults from
  existing config.
- submission mapping: outside/room/sensor dicts correct; `Skip` removes; manual entries for
  non-discovered sensors preserved; idempotent re-run.
- VTherm-without-area abort path; empty-discovery path.

## 8. Backward compatibility

- Existing `smart_pi_connections` (new and legacy shapes) are read for the `current`
  back-reference and preserved on merge. The manual flow is unchanged. No migration needed —
  discovery is purely additive and writes the established connection shape.

## 9. Files

- **New:** `smartpi/topology.py`, `tests/test_topology.py`, `tests/test_config_flow_discovery.py`.
- **Changed:** `config_flow.py` (menu + discovery step + submission mapping),
  `translations/{en,fr}.json` (menu labels, step title/description, field labels, abort/empty
  messages).
- **Unchanged:** the coupling model and all its state.
