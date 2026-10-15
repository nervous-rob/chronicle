"""chronicle.config — the run-configuration contract (RUN_PARAMS v2).

A run is (config, seed).  This module:
  1. loads run.default.toml, overlays a sparse user run.toml, applies dotted sweep overrides;
  2. loads the theme catalog and applies [catalog.*] overlays;
  3. validates structure (unknown keys are errors except in free-key sections), types, ranges,
     and cross-references (role ids, item ids, capability ids, event types, stage names);
  4. computes derived values (ticks, per-cell rates from real units, chronicler cadences);
  5. freezes everything and hashes it -> run_id.

Nothing in the engine reads TOML; it reads the Config object produced here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tomllib
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 2
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SEASONS = ("spring", "summer", "autumn", "winter")
SLEEP_RELATIONS = ("own", "household", "any")   # sleeper-to-place relation vocabulary for sleep.access rules
TERRAINS = ("water", "marsh", "plains", "forest", "hills", "mountains", "desert")
VISIBILITY = ("public", "private", "concealed")
WITNESS_SCOPES = ("place", "cell", "none")   # [events.witness]: who besides actors remembers a public event; "none" = no automatic memory at all

# Capability primitives: a [capabilities.<id>] table lists the primitives it `enables`; people.py checks
# the primitive, never a capability id. Agency verbs declare which primitive (if any) they require.
CAPABILITY_PRIMITIVES = ("record", "read", "copy", "plan")
TERRAIN_NAMES = ("water", "marsh", "plains", "forest", "hills", "mountains", "desert")
CAPABILITY_FIELDS = ("learn_days", "min_stage", "teachers", "needs_medium", "self_teach", "enables")
# Verb registry for the decision interface. timed = charged against the daily activity budget via
# agency.action_hours (validated to be exactly this set). Menu order follows this order.
VERBS = {
    "rest":  {"timed": False, "requires": None},
    "work":  {"timed": False, "requires": None},
    "go":    {"timed": False, "requires": None},
    "haul":  {"timed": False, "requires": None},
    "give":  {"timed": True,  "requires": None},   # pressure-v1: hand carried food to a co-present person (inventory.transfer)
    "hunt":  {"timed": True,  "requires": None},   # pressure-v1: Cells.sample_hunt -> carried meat, capped by carry room
    "firebreak": {"timed": True, "requires": None},
    "project": {"timed": False, "requires": None},  # pressure-v1: Cells.clear on the person's own cell (removes wild fuel)
    "explore": {"timed": True, "requires": None},   # survey one adjacent, personally unknown cell
    "say":   {"timed": True,  "requires": None},
    "tell":  {"timed": True,  "requires": None},   # memory-v1: pass one remembered event to a co-present person (people.tell, ledgered hearsay)
    "note":  {"timed": True,  "requires": "record"},
    "cite":  {"timed": True,  "requires": "record"},
    "read":  {"timed": True,  "requires": "read"},
    "teach": {"timed": True,  "requires": None},
    "plan":  {"timed": False, "requires": None},   # meta-verb: adopts a multi-day plan of ordinary verbs (plans-v1); never a plan step itself
}

# Event types the engine emits (ledger).  auto_record / salience / discovery keys must be in here.
EVENT_TYPES = frozenset("""
born conceived pregnancy_lost died bond unbond role_changed moved_home named stage
departed arrived passed_through encountered journey_abandoned journey_refused migrated
built repaired decayed destroyed burned
transfer hired paid default contract_done
say tell confess heard discovered
court lapsed withdraw convene cancel attend gathering
accuse judge confined released exiled
claim grant appoint collect_tax levy
declare vote tally term_end
steal take
confront injured killed
record read copy seal open
teach learned
name_thing hardened
locality_founded locality_abandoned
fire_ignited fire_spread harvest planted guard hunt forage
sick recovered
tax_collected
slept_rough found_asleep lodging_refused
scenario firebreak project_started project_worked project_completed
""".split())

# Sections whose keys are ids defined elsewhere (checked by cross-ref, not by schema).
FREE_KEY_SECTIONS = {
    "setup.literacy_by_role": "role+",
    "setup.household_buildings": "role+",
    "setup.media_by_role": "role",
    "capabilities.teach_weight": "role+",
    "time.work_days": "role+",
    "leisure.role_weights.naming": "role+",
    "gossip.salience": "event+",
    "secrets.discovery": "event",
    "resolver.base_p": "event",
    "justice.weights": "region+",
    "chroniclers.bias_prompts": "region+",
    "records.write_weight": "role+",
    "records.auto_record": "role",
    "leisure.trait_weights": "any",
    "leisure.court_private_trait_mods": "any",
    "succession.mode": "any",
    "catalog.items": "any", "catalog.buildings": "any", "catalog.roles": "any",
    "catalog.crops": "any", "catalog.wildlife": "any", "catalog.disease": "any",
    "sleep.access": "any", "sleep.consequences": "any",
    "events.witness": "event+",
}
# `capabilities.<id>` tables are free at the second level; handled specially.


class ConfigError(Exception):
    pass


# ----------------------------------------------------------------------------- loading

def _read_toml(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _deep_merge(base: dict, overlay: dict, path: str = "") -> dict:
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v, f"{path}{k}.")
        else:
            out[k] = copy.deepcopy(v)
    return out


def _set_dotted(d: dict, dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
        if not isinstance(cur, dict):
            raise ConfigError(f"override {dotted}: {k} is not a table")
    cur[keys[-1]] = value


def _path_exists(d: dict, dotted: str) -> bool:
    cur: Any = d
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return False
        cur = cur[k]
    return True


def _walk(d: dict, prefix: str = ""):
    for k, v in d.items():
        p = f"{prefix}{k}"
        yield p, v
        if isinstance(v, dict):
            yield from _walk(v, p + ".")


def _is_free(path: str) -> bool:
    for sec in FREE_KEY_SECTIONS:
        if path == sec or path.startswith(sec + "."):
            return True   # the container itself is free too, so a file overlay may introduce it
    if path.startswith("capabilities.") and path != "capabilities.teach_weight" and not path.startswith("capabilities.teach_weight."):
        return True   # a theme may add a whole [capabilities.<id>] table; its fields are checked against CAPABILITY_FIELDS below
    return False


# ----------------------------------------------------------------------------- validation helpers

def _req(d: dict, key: str, typ, path: str, lo=None, hi=None):
    if key not in d:
        raise ConfigError(f"missing {path}.{key}")
    v = d[key]
    if typ is float and isinstance(v, bool):
        raise ConfigError(f"{path}.{key}: expected number, got bool")
    if typ is float and isinstance(v, int):
        v = float(v)
    if not isinstance(v, typ):
        raise ConfigError(f"{path}.{key}: expected {typ.__name__}, got {type(v).__name__}")
    if lo is not None and v < lo:
        raise ConfigError(f"{path}.{key}={v} below minimum {lo}")
    if hi is not None and v > hi:
        raise ConfigError(f"{path}.{key}={v} above maximum {hi}")
    return v


def _season_table(v, path: str) -> dict:
    """Accept a scalar or a per-season table; return a full per-season dict."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return {s: float(v) for s in SEASONS}
    if isinstance(v, dict):
        missing = [s for s in SEASONS if s not in v]
        extra = [k for k in v if k not in SEASONS]
        if missing or extra:
            raise ConfigError(f"{path}: season table missing {missing} / unknown {extra}")
        return {s: float(v[s]) for s in SEASONS}
    raise ConfigError(f"{path}: expected number or season table")


# ----------------------------------------------------------------------------- the frozen config

@dataclass(frozen=True)
class Config:
    raw: dict                       # merged + validated run config (plain dict, immutable by convention)
    catalog: dict                   # merged theme catalog
    derived: dict                   # computed values
    run_id: str
    seed: int

    def __getitem__(self, section: str):
        return self.raw[section]

    def get(self, dotted: str, default=None):
        cur: Any = self.raw
        for k in dotted.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def role(self, role_id: str) -> dict:
        return self.catalog["roles"][role_id]

    def item(self, item_id: str) -> dict:
        return self.catalog["items"][item_id]

    def building(self, b_id: str) -> dict:
        return self.catalog["buildings"][b_id]

    def to_json(self) -> str:
        return json.dumps({"run": self.raw, "catalog": self.catalog, "derived": self.derived,
                           "run_id": self.run_id}, indent=1, sort_keys=True)


# ----------------------------------------------------------------------------- main entry

def load(user_path: str | None = None, overrides: dict[str, Any] | None = None,
         defaults_path: str | None = None, themes_dir: str | None = None) -> Config:
    defaults_path = defaults_path or os.path.join(ROOT, "run.default.toml")
    themes_dir = themes_dir or os.path.join(ROOT, "themes")
    defaults = _read_toml(defaults_path)
    user = _read_toml(user_path) if user_path else {}
    if "schema_version" in user and user["schema_version"] != SCHEMA_VERSION:
        raise ConfigError(f"schema_version {user['schema_version']} != {SCHEMA_VERSION}")

    # unknown-key check: every user key must exist in defaults unless inside a free-key section
    for path, _ in _walk(user):
        if _is_free(path):
            continue
        cur: Any = defaults
        ok = True
        for k in path.split("."):
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if not ok:
            raise ConfigError(f"unknown key {path} (not in run.default.toml and not a free-key section)")

    raw = _deep_merge(defaults, user)
    for k, v in (overrides or {}).items():
        if not _is_free(k) and not _path_exists(defaults, k):
            raise ConfigError(f"unknown override key {k}")
        _set_dotted(raw, k, v)

    catalog = _load_catalog(raw, themes_dir)
    _validate(raw, catalog)
    from .rng import LAYOUT_VERSION
    _req(raw["rng"], "layout_version", int, "rng", LAYOUT_VERSION, LAYOUT_VERSION)
    derived = _derive(raw, catalog)
    frozen = json.dumps({"run": raw, "catalog": catalog}, sort_keys=True, separators=(",", ":")).encode()
    run_id = hashlib.sha256(frozen).hexdigest()[:12]
    return Config(raw=raw, catalog=catalog, derived=derived, run_id=run_id, seed=int(raw["run"]["seed"]))


def _load_catalog(raw: dict, themes_dir: str) -> dict:
    theme = raw["world"]["theme"]
    path = os.path.join(themes_dir, theme, "catalog.toml")
    if not os.path.exists(path):
        raise ConfigError(f"theme {theme!r}: {path} not found")
    cat = _read_toml(path)
    for sec in ("roles", "items", "buildings", "crops", "wildlife", "disease"):
        cat.setdefault(sec, {})
        overlay = raw.get("catalog", {}).get(sec, {})
        if overlay:
            cat[sec] = _deep_merge(cat[sec], overlay)
    enabled = raw["catalog"]["enabled"]
    disabled = set(raw["catalog"]["disabled"])
    extra = set(raw["catalog"]["extra"])
    if enabled != "theme-default":
        keep = set(enabled) | extra
        for sec in ("buildings",):
            for k in list(cat[sec]):
                if k not in keep:
                    del cat[sec][k]
    for k in disabled:
        found = False
        for sec in ("buildings", "items", "roles"):
            if k in cat[sec]:
                del cat[sec][k]
                found = True
        if not found:
            raise ConfigError(f"catalog.disabled: {k!r} not in theme")
    for k in extra:
        if not any(k in cat[sec] for sec in ("buildings", "items", "roles")):
            raise ConfigError(f"catalog.extra: {k!r} not in theme")
    return cat


# ----------------------------------------------------------------------------- validation

def _validate(raw: dict, cat: dict) -> None:
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ConfigError("schema_version must be 2")
    roles = set(cat["roles"]); items = set(cat["items"]); buildings = set(cat["buildings"])
    caps = {k for k in raw["capabilities"] if k != "teach_weight"}
    stages = list(raw["stages"])

    r = raw["run"]
    _req(r, "seed", int, "run", 0)
    _req(r, "years", int, "run", 1)
    _req(r, "days_per_tick", int, "run", 1, 365)
    if r["origin"] not in ("founding", "established"):
        raise ConfigError("run.origin must be founding|established")

    u = raw["units"]
    _req(u, "cell_km", float, "units", 0.05, 50.0)
    _req(u, "food_unit_g", int, "units", 1)

    if raw["rng"]["generator"] != "philox":
        raise ConfigError("rng.generator: only 'philox' is supported")

    w = raw["world"]
    _req(w, "width_km", int, "world", 4); _req(w, "height_km", int, "world", 4)
    _req(w, "water_fraction", float, "world", 0.0, 0.9)
    _req(w, "mountain_fraction", float, "world", 0.0, 0.9)
    _req(w, "region_count", int, "world", 1)
    _req(w, "terrain_scale_km", float, "world", 0.5)
    _req(w, "terrain_octaves", int, "world", 1, 12)
    _req(w, "relief_m", float, "world", 1.0)
    lm = w["land_mix"]
    for k in ("hills", "forest", "marsh", "desert"):
        _req(lm, k, float, "world.land_mix", 0.0, 1.0)
    if sum(lm.values()) > 1.0 + 1e-9:
        raise ConfigError("world.land_mix fractions must sum to <= 1 (rest is plains)")
    for t in TERRAINS:
        if t not in w["terrain_cost"]: raise ConfigError(f"world.terrain_cost missing {t}")
        if t not in w["buildable"]: raise ConfigError(f"world.buildable missing {t}")

    t = raw["transit"]
    _req(t, "km_per_day", float, "transit", 0.1)
    if len(t["road_mult"]) != 4: raise ConfigError("transit.road_mult needs 4 entries (road_level 0..3)")
    _season_table(t["season_mult"], "transit.season_mult")

    p = raw["population"]
    _req(p, "initial", int, "population", 1)
    _req(p, "clusters", int, "population", 1)
    cw = p["cluster_weights"]
    if len(cw) != p["clusters"]:
        raise ConfigError("population.cluster_weights length must equal population.clusters")
    if abs(sum(cw) - 1.0) > 1e-6:
        raise ConfigError("population.cluster_weights must sum to 1")
    if abs(sum(p["household_size_dist"]) - 1.0) > 1e-6:
        raise ConfigError("population.household_size_dist must sum to 1")
    for k in p["roles_mix"]:
        if k != "other" and k not in roles:
            raise ConfigError(f"population.roles_mix: {k!r} is not a catalog role")
    if abs(sum(p["roles_mix"].values()) - 1.0) > 1e-6:
        raise ConfigError("population.roles_mix must sum to 1")
    if p["age_dist"] not in ("premodern", "young", "flat"):
        raise ConfigError("population.age_dist")
    _req(p, "birth_rate_per_year", (int, float), "population", 0.0, 2.0)
    _req(p, "death_base_per_year", (int, float), "population", 0.0, 2.0)

    # stages: strictly increasing, infant = 0
    vals = [raw["stages"][s] for s in stages]
    if stages[0] != "infant" or vals[0] != 0 or any(b <= a for a, b in zip(vals, vals[1:])):
        raise ConfigError("stages must start with infant = 0 and be strictly increasing")
    for s in stages:
        if s not in raw["development"]["stage_rate"]:
            raise ConfigError(f"development.stage_rate missing stage {s}")
        if s not in raw["needs"]["age_mult"]:
            raise ConfigError(f"needs.age_mult missing stage {s}")
        if s not in raw["development"]["stage_work_mult"]:
            raise ConfigError(f"development.stage_work_mult missing stage {s}")
        _req(raw["development"]["stage_work_mult"], s, float, "development.stage_work_mult", 0.0, 10.0)
    for s in raw["development"]["stage_work_mult"]:
        if s not in stages: raise ConfigError(f"development.stage_work_mult: unknown stage {s!r}")

    dm = raw["demography"]
    if not isinstance(dm["enabled"], bool): raise ConfigError("demography.enabled must be bool")
    _req(dm, "fertile_min_years", int, "demography", 0, 120)
    _req(dm, "gestation_days", int, "demography", 1, 730)
    _req(dm, "fertile_max_years", int, "demography", 0, 120)
    if dm["fertile_max_years"] < dm["fertile_min_years"]: raise ConfigError("demography fertile range inverted")
    _req(dm, "bond_chance_per_year", (int, float), "demography", 0.0, 1.0)
    if set(dm["mortality_mult"]) != set(stages): raise ConfigError("demography.mortality_mult must name every stage")
    for stage, value in dm["mortality_mult"].items(): _req(dm["mortality_mult"], stage, (int, float), "demography.mortality_mult", 0.0, 100.0)

    for cid in caps:
        c = raw["capabilities"][cid]
        for key in CAPABILITY_FIELDS[:-1]:
            if key not in c: raise ConfigError(f"capabilities.{cid} missing {key}")
        for key in c:
            if key not in CAPABILITY_FIELDS: raise ConfigError(f"capabilities.{cid}: unknown field {key!r}")
        en = c.get("enables", [])
        if not isinstance(en, list) or any(type(x) is not str for x in en):
            raise ConfigError(f"capabilities.{cid}.enables must be a list of primitive names")
        for prim in en:
            if prim not in CAPABILITY_PRIMITIVES: raise ConfigError(f"capabilities.{cid}.enables: unknown primitive {prim!r}")
        if c["min_stage"] not in stages: raise ConfigError(f"capabilities.{cid}.min_stage unknown stage")
        if c["teachers"] != "any_holder":
            for rid in c["teachers"]:
                if rid not in roles: raise ConfigError(f"capabilities.{cid}.teachers: {rid!r} not a role")

    def role_keys(section: dict, path: str, allow: tuple[str, ...]):
        for k in section:
            if k not in roles and k not in allow:
                raise ConfigError(f"{path}: {k!r} is not a catalog role")
    role_keys(raw["setup"]["literacy_by_role"], "setup.literacy_by_role", ("anyone",))
    role_keys(raw["setup"]["household_buildings"], "setup.household_buildings", ("anyone",))
    role_keys(raw["setup"]["media_by_role"], "setup.media_by_role", ())
    st = raw["setup"]
    _req(st, "literacy_scale", (int, float), "setup", 0.0, 10.0)
    _req(st, "historian_rate", (int, float), "setup", 0.0, 1.0)
    _req(st, "notable_starters", int, "setup", 0)
    _req(st, "pocket_coin", int, "setup", 0)
    if not isinstance(st["seed_media"], bool): raise ConfigError("setup.seed_media must be bool")
    if not isinstance(st["chronicler_role"], str) or (st["chronicler_role"] and st["chronicler_role"] not in roles):
        raise ConfigError("setup.chronicler_role must be \"\" or a catalog role")
    sr = st["subsistence_role"]
    if not isinstance(sr, str) or (sr and sr not in roles):
        raise ConfigError("setup.subsistence_role must be \"\" or a catalog role")
    if sr and not any(cat["items"][i]["kind"] == "food" for i in cat["roles"][sr]["produces"]):
        raise ConfigError("setup.subsistence_role must produce a food item")
    for k, v in st["household_buildings"].items():
        if not isinstance(v, list) or not all(isinstance(b, str) and b in cat["buildings"] for b in v):
            raise ConfigError(f"setup.household_buildings.{k}: list of catalog building ids required")
    if not any(cat["buildings"][b]["shelter"] > 0 for b in st["household_buildings"].get("anyone", [])):
        raise ConfigError("setup.household_buildings.anyone needs at least one building with shelter > 0")
    for k, v in st["media_by_role"].items():
        if not isinstance(v, str) or v not in cat["items"] or cat["items"][v]["kind"] != "medium":
            raise ConfigError(f"setup.media_by_role.{k}: must be a catalog medium item")
    for k, v in st["coin_by_wealth"].items():
        if type(v) is not int or v < 0: raise ConfigError(f"setup.coin_by_wealth.{k}: nonnegative int")
    if not st["coin_by_wealth"]: raise ConfigError("setup.coin_by_wealth needs at least one class")
    for k, v in st["literacy_by_role"].items():
        if not isinstance(v, (int, float)) or not 0 <= v <= 1: raise ConfigError(f"setup.literacy_by_role.{k}: 0..1")
    pp = raw["population"]
    _req(pp, "cluster_spread_km", (int, float), "population", 0.0)
    _req(pp, "food_buffer_months", (int, float), "population", 0.0)
    _req(pp, "wealth_gini", (int, float), "population", 0.0, 0.95)
    _req(pp, "initial_road_level", int, "population", 0, 3)
    if not isinstance(pp["family_seed"], bool): raise ConfigError("population.family_seed must be bool")
    for k, v in pp["cluster_terrain_prefs"].items():
        if k != "water_adjacent" and k not in TERRAIN_NAMES: raise ConfigError(f"population.cluster_terrain_prefs: {k!r}")
        if not isinstance(v, (int, float)) or v < 0: raise ConfigError(f"population.cluster_terrain_prefs.{k}: nonnegative")
    role_keys(raw["capabilities"]["teach_weight"], "capabilities.teach_weight", ("parent", "anyone"))
    role_keys(raw["time"]["work_days"], "time.work_days", ("default",))
    role_keys(raw["leisure"]["role_weights"]["naming"], "leisure.role_weights.naming", ("default",))
    role_keys(raw["records"]["write_weight"], "records.write_weight", ("anyone",))
    role_keys(raw["records"]["auto_record"], "records.auto_record", ())
    if "default" not in raw["time"]["work_days"]:
        raise ConfigError("time.work_days.default is required")
    for k, v in raw["time"]["work_days"].items():
        _season_table(v, f"time.work_days.{k}")

    for rid, evs in raw["records"]["auto_record"].items():
        for e in evs:
            if e != "*" and e not in EVENT_TYPES:
                raise ConfigError(f"records.auto_record.{rid}: unknown event type {e!r}")
    for e in raw["gossip"]["salience"]:
        base = e.split(":", 1)[0]
        if e != "default" and base not in EVENT_TYPES:
            raise ConfigError(f"gossip.salience: unknown event type {base!r} in key {e!r}")
    ew = raw["events"]["witness"]
    if "default" not in ew: raise ConfigError("events.witness.default is required")
    for k, v in ew.items():
        base = k.split(":", 1)[0]
        if k != "default" and base not in EVENT_TYPES:
            raise ConfigError(f"events.witness: unknown event type {base!r} in key {k!r}")
        if v not in WITNESS_SCOPES: raise ConfigError(f"events.witness.{k}: scope must be one of {WITNESS_SCOPES}")
    for e in raw["secrets"]["discovery"]:
        if e.split(":", 1)[0] not in EVENT_TYPES:
            raise ConfigError(f"secrets.discovery: unknown event type in key {e!r}")
    for e in raw["resolver"]["base_p"]:
        if e not in EVENT_TYPES:
            raise ConfigError(f"resolver.base_p: unknown event type {e!r}")

    sri = raw["records"]["seal_requires_item"]
    if sri and sri not in items:
        raise ConfigError(f"records.seal_requires_item {sri!r} is not a catalog item")
    if raw["succession"]["rule"] not in ("designated", "eldest_kin", "none"):
        raise ConfigError("succession.rule")
    for k, v in raw["succession"]["mode"].items():
        if v not in ("inherit", "elective", "appointed"):
            raise ConfigError(f"succession.mode.{k} must be inherit|elective|appointed")
    if raw["transfer"]["default_visibility"] not in VISIBILITY:
        raise ConfigError("transfer.default_visibility")
    for k in raw["justice"]["weights"].get("default", {}):
        if k not in ("dismiss", "fine", "restitution", "confine", "exile", "maim", "execute"):
            raise ConfigError(f"justice.weights.default: unknown outcome {k!r}")

    for sec in ("rain_mm_mean", "evaporation_mm", "lightning_p"):
        _season_table(raw["weather"][sec], f"weather.{sec}")
    _req(raw["weather"], "persistence", float, "weather", 0.0, 0.999)
    _req(raw["fire"], "base_hours", float, "fire", 0.01)
    for key in ("dispersal", "raid_rate", "predation_rate"):
        value = _req(raw["wildlife"], key, float, "wildlife", 0.0, 1.0)
        if not math.isfinite(value): raise ConfigError(f"wildlife.{key}: nonfinite")
    _req(raw["wildlife"], "habitat_coupling", bool, "wildlife")
    _req(raw["wildlife"], "prey_coupling", bool, "wildlife")
    _req(raw["wildlife"], "prey_refuge", bool, "wildlife")
    _req(raw["wildlife"], "prey_response", bool, "wildlife")
    for _k in ("response_fill", "response_floor"):
        _v = _req(raw["wildlife"], _k, float, "wildlife", 0.0, 1.0)
        if not math.isfinite(_v): raise ConfigError(f"wildlife.{_k}: nonfinite")
    if raw["wildlife"]["response_fill"] <= 0: raise ConfigError("wildlife.response_fill must be > 0")
    _rf = _req(raw["wildlife"], "refuge_fill", float, "wildlife", 0.0, 1.0)
    if not math.isfinite(_rf) or _rf <= 0: raise ConfigError("wildlife.refuge_fill must be finite and > 0")
    for key in ("prey_satiation", "starvation_rate"):
        value = _req(raw["wildlife"], key, float, "wildlife", 0.0, 1.0)
        if not math.isfinite(value): raise ConfigError(f"wildlife.{key}: nonfinite")
    if raw["wildlife"]["prey_satiation"] <= 0: raise ConfigError("wildlife.prey_satiation must be > 0")
    _req(raw["wildlife"], "prey_intake", bool, "wildlife")
    _ic = _req(raw["wildlife"], "intake_cap", float, "wildlife", 0.0, 1.0)
    if not math.isfinite(_ic) or _ic <= 0: raise ConfigError("wildlife.intake_cap must be finite and > 0")
    _is = _req(raw["wildlife"], "intake_starve", float, "wildlife", 0.0, 1.0)
    if not math.isfinite(_is): raise ConfigError("wildlife.intake_starve: nonfinite")
    if raw["wildlife"]["prey_intake"] and (raw["wildlife"]["prey_coupling"] or raw["wildlife"]["prey_response"]):
        raise ConfigError("wildlife.prey_intake replaces prey_coupling and prey_response; enable at most one of them")
    _req(raw["fire"], "burnout", float, "fire", 0.0, 1.0)
    _req(raw["fire"], "min_effective_fuel", float, "fire", 0.0, 1.0)
    _req(raw["fire"], "building_clearing", float, "fire", 0.0, 1.0)
    _req(raw["observation"], "obs_alpha", float, "observation", 0.0, 1.0)
    _req(raw["validator"], "conservation_sample", float, "validator", 0.0, 1.0)
    th = raw["needs"]["thresholds"]
    if not (0 < th["critical"] < th["low"] < 1):
        raise ConfigError("needs.thresholds: 0 < critical < low < 1")
    sc = raw["scarcity"]
    if not isinstance(sc["enabled"], bool) or not isinstance(sc["theft_enabled"], bool):
        raise ConfigError("scarcity.enabled/theft_enabled must be bool")
    _req(sc, "theft_hours", float, "scarcity", 0.0, raw["agriculture"]["activity_hours_per_day"])
    hb = raw["hire"]["tenure_mult"]
    if any(b[0] <= a[0] for a, b in zip(hb, hb[1:])) or hb[0][0] != 0:
        raise ConfigError("hire.tenure_mult breakpoints must start at 0 and increase")
    for k in ("address_every_years", "locality_every_years", "region_every_years",
              "country_every_years", "person_every_years"):
        _req(raw["chroniclers"], k, float, "chroniclers", 0.01)
    _req(raw["attention"], "k_per_tick", int, "attention", 1)
    _req(raw["attention"], "decay", float, "attention", 0.0, 1.0)
    _req(raw["attention"], "k_cap", int, "attention", 1)
    _req(raw["attention"], "k_per_pop", float, "attention", 0.0, 1.0)
    _req(raw["attention"], "debt_norm_days", float, "attention", 1.0)
    _req(raw["attention"], "scene_max_people", int, "attention", 2)
    if raw["attention"].get("min_stage") not in raw["stages"]:
        raise ConfigError(f"attention.min_stage must be one of {sorted(raw['stages'])}")
    for k in ("deviation", "social", "role", "debt", "alarm"):
        _req(raw["attention"]["weights"], k, float, "attention.weights", 0.0, 1000.0)

    # ---- exploration-v1: personal map survey duration
    ex = raw["exploration"]
    _req(ex, "survey_hours", float, "exploration", 0.000001, raw["agriculture"]["activity_hours_per_day"])

    # ---- pressure-v1: agency verbs that need parameters, and the off-by-default scenario schedule
    ag2 = raw["agency"]
    if not isinstance(ag2.get("hunt_item"), str) or not ag2["hunt_item"]: raise ConfigError("agency.hunt_item must be a non-empty item id")
    _req(ag2, "firebreak_clear", float, "agency", 0.0, 1.0)
    sc = raw["scenario"]
    if type(sc.get("enabled")) is not bool: raise ConfigError("scenario.enabled must be true or false")
    spec = {"drought": ("start_day", "days", "evaporation_mult", "rain_mult"), "ignition": ("day", "distance_cells", "count"),
            "blight": ("day", "loss"), "stranger": ("day", "count", "ration_kg")}
    for name, keys in spec.items():
        tab = sc.get(name)
        if not isinstance(tab, dict) or set(tab) != set(keys): raise ConfigError(f"scenario.{name} must have exactly the keys {list(keys)}")
        for k in keys:
            v = tab[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < (-1 if k in ("start_day", "day") else 0):
                raise ConfigError(f"scenario.{name}.{k} must be a finite number (day -1 = off)")
    if not 0 <= sc["blight"]["loss"] <= 1: raise ConfigError("scenario.blight.loss must lie in 0..1")

    # ---- llm adapter (increment F)
    lm = raw["llm"]
    for k in ("url", "model", "keep_alive"):
        if not isinstance(lm.get(k), str) or not lm[k]: raise ConfigError(f"llm.{k} must be a non-empty string")
    if not lm["url"].startswith(("http://", "https://")): raise ConfigError("llm.url must start with http:// or https://")
    _req(lm, "temperature", float, "llm", 0.0, 2.0)
    _req(lm, "timeout_s", float, "llm", 1.0, 3600.0)
    _req(lm, "retries", int, "llm", 0, 10)
    _req(lm, "num_ctx", int, "llm", 512, 1048576)
    _req(lm, "num_predict", int, "llm", 16, 32768)
    if type(lm.get("think")) is not bool: raise ConfigError("llm.think must be true or false")
    if lm.get("format") not in ("schema", "json", "none"): raise ConfigError('llm.format must be "schema", "json" or "none"')
    if lm.get("on_invalid") not in ("routine", "rest"): raise ConfigError('llm.on_invalid must be "routine" or "rest"')

    pr = raw["projects"]
    for k in ("clear_labor_hours", "extract_labor_hours"):
        _req(pr, k, float, "projects", 0.000001)
    _req(pr, "clear_fraction", float, "projects", 0.000001, 1.0)
    _req(pr, "timber_per_extract", int, "projects", 1)

    ag = raw["agriculture"]
    for k in ("activity_hours_per_day", "plant_labor_days_per_ha", "harvest_labor_days_per_ha", "handling_kg_per_hour", "same_cell_place_hours"):
        v = _req(ag, k, float, "agriculture", 0.000001)
        if not math.isfinite(v): raise ConfigError(f"agriculture.{k}: nonfinite")
    if ag["activity_hours_per_day"] > raw["units"]["day_hours"]:
        raise ConfigError("agriculture activity budget exceeds day")
    if ag["same_cell_place_hours"] > ag["activity_hours_per_day"]:
        raise ConfigError("same-cell movement exceeds activity budget")
    _req(ag, "pantry_days", int, "agriculture", 1)
    agn = raw["agency"]
    _req(agn, "packet_memory_limit", int, "agency", 1, 10000)
    timed = {k for k, v in VERBS.items() if v["timed"]}
    if set(agn["action_hours"]) != timed:
        raise ConfigError(f"agency.action_hours must define exactly the timed verbs {sorted(timed)}")
    for k in agn["action_hours"]:
        v = _req(agn["action_hours"], k, float, "agency.action_hours", 0.0, ag["activity_hours_per_day"])
        if not math.isfinite(v): raise ConfigError(f"agency.action_hours.{k}: nonfinite")

    # ---- plans (plans-v1): caps are data; the engine truncates, never judges content
    pl = raw["plans"]
    _req(pl, "max_chars", int, "plans", 1, 4000)
    for k, lo in (("base_steps", 1), ("base_horizon_days", 1), ("steps_per_level", 0), ("horizon_days_per_level", 0), ("max_assumptions", 0)):
        _req(pl, k, int, "plans", lo, 10000)
    for k in ("goal_kinds", "surprise_events"):
        v = pl.get(k)
        if not isinstance(v, list) or not v or any(type(x) is not str for x in v) or len(set(v)) != len(v):
            raise ConfigError(f"plans.{k} must be a non-empty list of distinct strings")
    for t in pl["surprise_events"]:
        if t not in EVENT_TYPES: raise ConfigError(f"plans.surprise_events: unknown event type {t!r}")

    # ---- contact + gossip (contact-v1): rates and caps are data; nothing here reads a person tier
    ct = raw["contact"]
    _req(ct, "slots", int, "contact", 0, 65535)
    _req(ct, "hours_per_exchange", float, "contact", 0.0, ag["activity_hours_per_day"])
    _req(ct, "send_cap", int, "contact", 0, 10000)
    _req(ct, "receive_cap", int, "contact", 0, 10000)
    _req(ct, "p_base", float, "contact", 0.0, 1.0)
    _req(ct, "commons_mult", float, "contact", 0.0, 1.0)
    _req(ct, "same_household_mult", float, "contact", 0.0, 1000.0)
    gs = raw["gossip"]
    _req(gs, "rate_days", int, "gossip", 1)
    for k in ("crowd_fidelity", "tell_fidelity_mult", "hop_decay", "overhear_p"):
        _req(gs, k, float, "gossip", 0.0, 1.0)
    for k, v in gs["salience"].items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or not math.isfinite(float(v)):
            raise ConfigError(f"gossip.salience.{k} must be a non-negative number")
    if "default" not in gs["salience"]: raise ConfigError("gossip.salience.default is required")

    # ---- sleep (data-driven access rules + consequence rows; engine walks tables, never names kinds)
    sl = raw["sleep"]
    _req(sl, "reference_shelter", float, "sleep", 0.01)
    _req(sl, "condition_weight", float, "sleep", 0.0, 1.0)
    _req(sl, "rest_recovery_per_quality", float, "sleep", 0.0)
    _req(sl, "shelter_need_per_quality", float, "sleep", 0.0)
    _season_table(sl["open_air_mult"], "sleep.open_air_mult")
    _req(sl, "wet_mult", float, "sleep", 0.0, 1.0)
    _req(sl, "ration_route_margin_hours", float, "sleep", 0.0)
    _req(sl, "ration_refuse", bool, "sleep")
    acc = sl["access"]
    if not isinstance(acc, dict) or not acc: raise ConfigError("sleep.access: at least one rule required")
    prios = []
    for rid, rule in acc.items():
        path = f"sleep.access.{rid}"
        if not isinstance(rule, dict): raise ConfigError(f"{path}: expected table")
        allowed = {"priority", "relation", "affordance", "min_shelter", "rent", "quality", "event", "witness_owner", "open_air"}
        extra = set(rule) - allowed
        if extra: raise ConfigError(f"{path}: unknown field(s) {sorted(extra)}")
        _req(rule, "priority", int, path)
        prios.append(rule["priority"])
        if rule.get("relation", "any") not in SLEEP_RELATIONS:
            raise ConfigError(f"{path}.relation must be one of {SLEEP_RELATIONS}")
        if not isinstance(rule.get("affordance", ""), str): raise ConfigError(f"{path}.affordance: expected str")
        _req(rule, "quality", float, path, 0.0)
        if "min_shelter" in rule: _req(rule, "min_shelter", float, path, 0.0)
        if "rent" in rule: _req(rule, "rent", bool, path)
        if "witness_owner" in rule: _req(rule, "witness_owner", bool, path)
        if "open_air" in rule: _req(rule, "open_air", bool, path)
        ev = rule.get("event", "")
        if ev and ev not in EVENT_TYPES: raise ConfigError(f"{path}.event: unknown event type {ev!r}")
        if rule.get("open_air") and rule.get("affordance"):
            raise ConfigError(f"{path}: open_air rules cannot require a building affordance")
    if len(set(prios)) != len(prios): raise ConfigError("sleep.access: priorities must be unique")
    if not any(r.get("open_air") for r in acc.values()):
        raise ConfigError("sleep.access: one open_air fallback rule is required (someone must be able to sleep anywhere)")
    cons = sl["consequences"]
    if not isinstance(cons, dict): raise ConfigError("sleep.consequences: expected table")
    for cid, row in cons.items():
        path = f"sleep.consequences.{cid}"
        if not isinstance(row, dict): raise ConfigError(f"{path}: expected table")
        allowed = {"when", "quality_below", "quality_at_least", "seasons", "consecutive_nights", "rest_below", "rest_at_least",
                   "hours_mult", "health_drain", "cause"}
        extra = set(row) - allowed
        if extra: raise ConfigError(f"{path}: unknown field(s) {sorted(extra)}")
        if not any(k in row for k in ("hours_mult", "health_drain")):
            raise ConfigError(f"{path}: needs an effect (hours_mult and/or health_drain)")
        for k in ("quality_below", "quality_at_least", "rest_below", "rest_at_least"):
            if k in row: _req(row, k, float, path, 0.0)
        if "consecutive_nights" in row: _req(row, "consecutive_nights", int, path, 1)
        if "seasons" in row:
            if not isinstance(row["seasons"], list) or any(s not in SEASONS for s in row["seasons"]):
                raise ConfigError(f"{path}.seasons: list of {SEASONS}")
        if "hours_mult" in row: _req(row, "hours_mult", float, path, 0.0, 1.0)
        if "health_drain" in row:
            _req(row, "health_drain", float, path, 0.0, 1.0)
            if not isinstance(row.get("cause", ""), str) or not row.get("cause"):
                raise ConfigError(f"{path}: health_drain rows must name a cause")
    _validate_catalog(raw, cat)


def _validate_catalog(raw: dict, cat: dict) -> None:
    roles, items, buildings = cat["roles"], cat["items"], cat["buildings"]
    caps = {k for k in raw["capabilities"] if k != "teach_weight"}
    for rid, r in roles.items():
        for key in ("workplace", "requires", "produces", "role_weight", "enforcement"):
            if key not in r: raise ConfigError(f"roles.{rid} missing {key}")
        if r["workplace"] != "none" and r["workplace"] not in buildings:
            raise ConfigError(f"roles.{rid}.workplace {r['workplace']!r} not a building")
        for c in r["requires"]:
            if c not in caps: raise ConfigError(f"roles.{rid}.requires: unknown capability {c!r}")
        for it in r["produces"]:
            if it not in items: raise ConfigError(f"roles.{rid}.produces: unknown item {it!r}")
    kinds = ("coin", "food", "material", "tool", "medium", "livestock", "vehicle")
    if not any(v["kind"] == "coin" for v in items.values()):
        raise ConfigError("catalog needs at least one item of kind coin")
    for iid, it in items.items():
        for key in ("kind", "weight", "spoil_per_day", "price"):
            if key not in it: raise ConfigError(f"items.{iid} missing {key}")
        if it["kind"] not in kinds: raise ConfigError(f"items.{iid}.kind {it['kind']!r}")
        if it["weight"] < 0 or it["price"] < 0: raise ConfigError(f"items.{iid}: negative weight/price")
        if it["kind"] == "medium":
            for key in ("capacity", "decay_p_per_day", "reusable", "fireproof"):
                if key not in it: raise ConfigError(f"items.{iid} (medium) missing {key}")
            if it["capacity"] < 1: raise ConfigError(f"items.{iid}.capacity < 1")
        if it["kind"] == "food" and abs(it["weight"] - raw["units"]["food_unit_g"] / 1000) > 1e-9:
            raise ConfigError(f"items.{iid}: food weight must equal units.food_unit_g/1000 kg")
        if it["kind"] == "livestock":
            for key in ("produces", "fodder_per_day", "breed_season", "breed_p"):
                if key not in it: raise ConfigError(f"items.{iid} (livestock) missing {key}")
            for out in it["produces"]:
                if out not in items: raise ConfigError(f"items.{iid}.produces: unknown item {out!r}")
    for bid, b in buildings.items():
        for key in ("terrain_allow", "requires", "store", "shelter", "confine_cap", "spoil_mult",
                    "affordances", "labor_days", "materials", "decay_per_day", "value"):
            if key not in b: raise ConfigError(f"buildings.{bid} missing {key}")
        for t in b["terrain_allow"]:
            if t not in TERRAINS: raise ConfigError(f"buildings.{bid}.terrain_allow: {t!r}")
        for m in b["materials"]:
            if m not in items: raise ConfigError(f"buildings.{bid}.materials: unknown item {m!r}")
        if "lodging_rate" in b:
            rate = _req(b, "lodging_rate", float, f"buildings.{bid}", 0.0)
            if not math.isfinite(rate): raise ConfigError(f"buildings.{bid}.lodging_rate: nonfinite")
    for cid, c in cat["crops"].items():
        for key in ("item", "plant_season", "harvest_season", "growth_days", "moisture_min",
                    "moisture_optimal", "moisture_max", "yield_kg_per_ha", "hectares_per_field",
                    "stress_loss_per_day"):
            if key not in c: raise ConfigError(f"crops.{cid} missing {key}")
        sl = _req(c, "stress_loss_per_day", float, f"crops.{cid}", 0.0)
        if not (0.0 <= sl < 1.0): raise ConfigError(f"crops.{cid}.stress_loss_per_day must be in [0,1)")
        if c["item"] not in items: raise ConfigError(f"crops.{cid}.item unknown")
        if not (0 <= c["moisture_min"] < c["moisture_optimal"] < c["moisture_max"] <= 1):
            raise ConfigError(f"crops.{cid}: moisture_min < optimal < max in [0,1]")
    for wid, wl in cat["wildlife"].items():
        for key in ("r", "k_per_km2", "diet", "raids", "hunt_yield_kg"):
            if key not in wl: raise ConfigError(f"wildlife.{wid} missing {key}")
        for key in ("r", "k_per_km2"):
            value = _req(wl, key, float, f"wildlife.{wid}", 0.0)
            if not math.isfinite(value): raise ConfigError(f"wildlife.{wid}.{key}: nonfinite")
    hosts_ok = set(cat["wildlife"]) | {i for i, v in items.items() if v["kind"] == "livestock"} | {"people"}
    for did, d in cat["disease"].items():
        for key in ("hosts", "routes", "beta", "cross", "incubation_days", "infectious_days",
                    "mortality", "immunity", "effects", "symptoms"):
            if key not in d: raise ConfigError(f"disease.{did} missing {key}")
        for h in d["hosts"]:
            if h not in hosts_ok: raise ConfigError(f"disease.{did}.hosts: {h!r} is not a population")
            if h not in d["beta"] or h not in d["mortality"]:
                raise ConfigError(f"disease.{did}: beta/mortality missing host {h!r}")
        for pair in d["cross"]:
            a, _, b = pair.partition(">")
            if a not in d["hosts"] or b not in d["hosts"]:
                raise ConfigError(f"disease.{did}.cross {pair!r}: both sides must be listed hosts")
    # setup lint-ables that are pure catalog: auto_record roles that require literacy exist
    for rid in raw["records"]["auto_record"]:
        if rid not in roles: raise ConfigError(f"records.auto_record: {rid!r} not a role")


# ----------------------------------------------------------------------------- derived

def _derive(raw: dict, cat: dict) -> dict:
    dpt = raw["run"]["days_per_tick"]
    years = raw["run"]["years"]
    total_days = years * 365
    total_ticks = math.ceil(total_days / dpt)
    cell_km = raw["units"]["cell_km"]
    cell_area_km2 = math.sqrt(3) / 2 * cell_km ** 2   # regular hex, flat-to-flat = cell_km
    ticks_per_year = 365 / dpt
    chron = raw["chroniclers"]
    chron_ticks = {g: max(1, round(chron[f"{g}_every_years"] * ticks_per_year))
                   for g in ("address", "locality", "region", "country", "person")}
    food_unit_g = raw["units"]["food_unit_g"]
    consumption_units = max(1, round(raw["needs"]["consumption_g_per_day"] / food_unit_g))
    grid_w = math.ceil(raw["world"]["width_km"] / cell_km)
    grid_h = math.ceil(raw["world"]["height_km"] / (cell_km * math.sqrt(3) / 2))
    stages = list(raw["stages"])
    stage_days = {s: int(raw["stages"][s] * 365) for s in stages}
    wildlife_k = {w: v["k_per_km2"] * cell_area_km2 for w, v in cat["wildlife"].items()}
    crop_fields = {c: v["yield_kg_per_ha"] * v["hectares_per_field"] * 1000 / food_unit_g
                   for c, v in cat["crops"].items()}  # max food units per field at optimal moisture
    kappa = raw["attention"]
    k_at_start = min(kappa["k_cap"], max(kappa["k_per_tick"], round(kappa["k_per_pop"] * raw["population"]["initial"])))
    est_gpu_hours = k_at_start * total_ticks * raw["llm"]["avg_scene_seconds"] / 3600
    warnings = []
    if dpt < 7 and raw["population"]["initial"] > 200:
        warnings.append("days_per_tick < 7 with initial > 200: LLM budget will be large")
    if est_gpu_hours > raw["llm"]["gpu_hours_budget"]:
        warnings.append(f"estimated {est_gpu_hours:.0f} GPU-h exceeds budget {raw['llm']['gpu_hours_budget']}; K will be throttled")
    return {
        "total_days": total_days,
        "total_ticks": total_ticks,
        "ticks_per_year": ticks_per_year,
        "cell_area_km2": cell_area_km2,
        "cell_cross_hours_base": raw["fire"]["base_hours"] * cell_km,
        "cells_per_day_plains": raw["transit"]["km_per_day"] / cell_km,
        "grid_w": grid_w, "grid_h": grid_h, "cells": grid_w * grid_h,
        "chronicler_ticks": chron_ticks,
        "consumption_units_per_day": consumption_units,
        "stage_days": stage_days,
        "stage_order": stages,
        "wildlife_k_per_cell": wildlife_k,
        "crop_units_per_field_max": crop_fields,
        "k_at_start": k_at_start,
        "est_gpu_hours": est_gpu_hours,
        "warnings": warnings,
    }


# ----------------------------------------------------------------------------- sweeps

def load_sweep(sweep_path: str) -> list[tuple[str, Config]]:
    sw = _read_toml(sweep_path)["sweep"]
    base = os.path.join(os.path.dirname(os.path.abspath(sweep_path)), sw["base"])
    out = []
    for v in sw.get("variants", []):
        out.append((v["name"], load(base, overrides=v.get("overrides", {}))))
    return out


if __name__ == "__main__":
    import sys
    cfg = load(sys.argv[1] if len(sys.argv) > 1 else None)
    print("run_id", cfg.run_id)
    print(json.dumps(cfg.derived, indent=1, sort_keys=True))
