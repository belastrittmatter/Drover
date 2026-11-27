#!/usr/bin/env python3
"""drover-config.py — the project config, its schema and the harness entries.

    drover config path                  print the config path (<main checkout>/.drover/config.json)
    drover config home                  print the drover install folder (workflows/, skills/, hooks/)
    drover config slug                  print the run slug: the repo name (FR-CFG-1, FR-MET-1)
    drover config validate [FILE]       validate against config-schema.json (FR-CFG-2)
    drover config harness [NAME]        print harnesses/<NAME>.json (default: the config's harness)
    drover config worker-argv ROLE PROMPT [MODEL]   NUL-separated argv of one headless worker (the runner)
    drover config model ROLE [PICK] [--builder M]   the model a worker of ROLE starts on ("" = harness default)
    drover skills install [--dest DIR]  link drover's skills into the harness's user skill folder

DROVER_CONFIG overrides the config path. `validate` with no config file exits 0: commands
that need one say so themselves. Exit: 0 ok, 64 usage, 65 invalid config, 66 missing
harness entry, 1 a skill link was left alone. Stdlib only (Python 3.9).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
SCHEMA = HOME / "skills/drover/references/config-schema.json"
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
         "integer": int, "number": (int, float)}


def root(cwd=None):
    """The main checkout, also from inside a worktree under .drover/worktrees/."""
    r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        return Path(cwd or ".").resolve()
    common = (Path(cwd or ".") / r.stdout.strip()).resolve()
    if common.name == ".git":
        return common.parent
    # A git dir kept elsewhere (--separate-git-dir, a submodule) gives no main checkout path:
    # use this checkout's top level.
    # shortcut: a linked worktree of such a repo resolves to itself; resolve the main checkout if that ever matters.
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True)
    return Path(top.stdout.strip()).resolve() if top.returncode == 0 and top.stdout.strip() else Path(cwd or ".").resolve()


def config_path(cwd=None):
    env = os.environ.get("DROVER_CONFIG")
    return Path(env) if env else root(cwd) / ".drover/config.json"


def slug(cwd=None):
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=cwd, capture_output=True, text=True)
    name = r.stdout.strip().rstrip("/").split("/")[-1].split(":")[-1] if r.returncode == 0 else ""
    name = re.sub(r"\.git$", "", name) or root(cwd).name
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)


def _type_ok(value, names):
    for name in names if isinstance(names, list) else [names]:
        if isinstance(value, bool) and name in ("integer", "number"):
            continue
        if isinstance(value, TYPES[name]):
            return True
    return False


def errors(value, schema, where="", top=None):
    """Every way `value` fails `schema`, as 'field: message'. A JSON Schema subset:
    $ref (local), type, enum, required, properties, additionalProperties, items,
    minimum, maximum, minLength, pattern."""
    top = top or schema
    if "$ref" in schema:
        schema = top["definitions"][schema["$ref"].rsplit("/", 1)[-1]]
    field = where or "(config)"
    if "type" in schema and not _type_ok(value, schema["type"]):
        return [f"{field}: expected {schema['type']}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{field}: {json.dumps(value)} is not one of {', '.join(map(json.dumps, schema['enum']))}"]
    out = []
    if isinstance(value, dict):
        props = schema.get("properties", {})
        out += [f"{where + '.' if where else ''}{k}: required" for k in schema.get("required", []) if k not in value]
        for key, item in value.items():
            sub = f"{where}.{key}" if where else key
            if key in props:
                out += errors(item, props[key], sub, top)
            elif schema.get("additionalProperties") is False:
                out.append(f"{sub}: unknown key")
            elif isinstance(schema.get("additionalProperties"), dict):
                out += errors(item, schema["additionalProperties"], sub, top)
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out += errors(item, schema["items"], f"{where}[{i}]", top)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{field}: must not be empty")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{field}: {json.dumps(value)} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{field}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{field}: must be <= {schema['maximum']}")
    return out


def validate(path):
    """Return a list of problems with the config at `path`; [] when valid."""
    try:
        cfg = json.loads(Path(path).read_text())
    except ValueError as e:
        return [f"(config): not valid JSON ({e})"]
    except OSError as e:
        return [f"(config): cannot be read ({e.strerror or e})"]
    return errors(cfg, json.loads(SCHEMA.read_text()))


def harness(name):
    """The harness entry harnesses/<name>.json. A missing entry halts (FR-HRN-2)."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name or ""):
        sys.exit(f"drover: invalid harness name {name!r}")
    path = HOME / "harnesses" / f"{name}.json"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in (HOME / "harnesses").glob("*.json"))) or "none"
        print(f"drover: no harness entry for '{name}' ({path} missing; known: {known}). "
              f"Set \"harness\" in .drover/config.json to a known one.", file=sys.stderr)
        sys.exit(66)
    return json.loads(path.read_text())


def configured_harness(cwd=None):
    p = config_path(cwd)
    cfg = json.loads(p.read_text()) if p.is_file() else {}
    return cfg.get("harness", "claude")


def read_only(entry):
    """Path rules for the folders a worker may read but never edit (the drover install).
    `//` starts an absolute path in a Claude permission rule. Claude's `Edit(path)` rule covers
    every file-editing tool and rejects path rules on Write, MultiEdit and NotebookEdit."""
    perms = entry.get("permissions", {})
    dirs = [d.replace("{drover_home}", str(HOME)) for d in perms.get("read_only_dirs", [])]
    return ([f"Read(//{d.lstrip('/')}/**)" for d in dirs],
            [f"{tool}(//{d.lstrip('/')}/**)" for d in dirs for tool in perms.get("read_only_deny", [])])


def allowed(entry, cfg):
    """The permission rules a headless worker starts with (FR-HRN-4): the harness's tools,
    one Bash rule per command word (the harness's, then the config's permissions.allow_commands),
    the first word of each project verify command, and Read on the read-only folders. The allow
    list is broad on purpose: hooks hold the boundary."""
    perms = entry.get("permissions", {})
    words = list(perms.get("commands", []))
    words += [w for w in (cfg.get("permissions") or {}).get("allow_commands", []) if w not in words]
    for cmd in cfg.get("verify_commands") or []:
        for part in re.split(r"&&|\|\||;|\|", cmd):
            first = (part.split() or [""])[0]
            if first and first not in words and re.fullmatch(r"[A-Za-z0-9._/-]+", first):
                words.append(first)
    return list(perms.get("tools", [])) + [f"Bash({w}:*)" for w in words] + read_only(entry)[0]


def agent_allowed(agent, cfg=None):
    """The read-only agent profile's rules: its tools, one Bash rule per read command, and per
    permissions.allow_commands wrapper word w (a hook that rewrites `git log` to `w git log`)
    one `w <read command>` rule. Never a bare `w`: that would let `w git commit` through."""
    cmds = list(agent.get("commands", []))
    wrappers = ((cfg or {}).get("permissions") or {}).get("allow_commands", [])
    cmds += [f"{w} {c}" for w in wrappers for c in agent.get("commands", [])]
    return list(agent.get("tools", [])) + [f"Bash({c}:*)" for c in cmds]


REVIEW_MODEL, REVIEW_ALT = "sonnet", "opus"
FAMILIES = ("sonnet", "opus", "haiku", "fable")


def family(model):
    """A model's family, so "claude-sonnet-5-5" and "sonnet" count as the same; else the raw id."""
    m = (model or "").lower()
    return next((f for f in FAMILIES if f in m), m)


def role_model(cfg, role, pick=None, builder=None):
    """The model a worker of `role` starts on; "" is the harness default. Build and qa: the
    role's model, else `pick` (the runner's ladder cell by card complexity). Review (roles.md
    FR-REV-13): roles.review.model, or roles.review.alt_model when that equals the model the
    card's builder ran on, so reviewer and builder always differ."""
    rcfg = (cfg.get("roles") or {}).get(role) or {}
    if role != "review":
        return rcfg.get("model") or pick or ""
    model = rcfg.get("model") or REVIEW_MODEL
    if builder and family(model) == family(builder):
        model = rcfg.get("alt_model") or REVIEW_ALT
        if family(model) == family(builder):
            print(f"drover: roles.review.model and roles.review.alt_model both equal the builder's model "
                  f"{builder!r}; set roles.review.alt_model to another model", file=sys.stderr)
            raise SystemExit(65)
    return model


def load_config(cwd=None):
    p = config_path(cwd)
    return json.loads(p.read_text()) if p.is_file() else {}


def worker_argv(role, prompt, cwd=None, model=None):
    """argv for one headless worker of `role`: the harness command, the prompt, `model` (the
    runner resolves it with role_model; else the role's own) and the permissions. The runner
    reads it NUL-separated."""
    cfg = load_config(cwd)
    rcfg = (cfg.get("roles") or {}).get(role) or {}
    entry = harness(rcfg.get("harness") or cfg.get("harness") or "claude")
    argv = list(entry["worker_command"]) + [prompt]
    model = model or role_model(cfg, role)
    if model and entry.get("model_flag"):
        argv += [entry["model_flag"], model]
    perms = entry.get("permissions", {})
    agent = entry.get("agent") or {}
    if role in agent.get("roles", []):
        # The runner's short read-only agents (pre-flight, classifier): JSON output, read tools and
        # read commands only, nothing else is approved; the runner does every write.
        argv += list(agent.get("output_flags", []))
        if agent.get("mode"):
            argv += [perms["mode_flag"], agent["mode"]]
        argv += [perms["allow_flag"], ",".join(agent_allowed(agent, cfg))]
        if agent.get("deny"):
            argv += [perms["deny_flag"], ",".join(agent["deny"])]
        return argv
    if perms.get("mode"):
        argv += [perms["mode_flag"], perms["mode"]]
    argv += [perms["allow_flag"], ",".join(allowed(entry, cfg))]
    deny = list(perms.get("deny", [])) + read_only(entry)[1]
    if deny:
        argv += [perms["deny_flag"], ",".join(deny)]
    return argv


def skills_install(dest=None, name=None):
    """Link every skill in <drover>/skills into the harness's user skill folder (FR-CFG-7).
    An existing link to the same skill is kept; anything else there is left alone."""
    entry = harness(name or configured_harness())
    target = Path(os.path.expanduser(dest or entry["user_skill_dir"]))
    target.mkdir(parents=True, exist_ok=True)
    kept = 0
    for src in sorted(p for p in (HOME / "skills").iterdir() if (p / "SKILL.md").is_file()):
        link = target / src.name
        if link.is_symlink() and link.resolve() == src.resolve():
            print(f"ok {src.name}")
            continue
        if link.is_symlink() and not link.exists():
            link.unlink()  # a dangling link from an older install folder
        if link.exists() or link.is_symlink():
            print(f"left alone {link}: not a link to {src}", file=sys.stderr)
            kept += 1
            continue
        link.symlink_to(src)
        print(f"linked {src.name}")
    return 1 if kept else 0


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "path":
        print(config_path())
    elif cmd == "home":
        print(HOME)
    elif cmd == "slug":
        print(slug())
    elif cmd == "validate":
        path = Path(argv[1]) if len(argv) > 1 else config_path()
        if not path.is_file():
            return 0 if len(argv) == 1 else 66
        problems = validate(path)
        for p in problems:
            print(f"drover: invalid config {path}: {p}", file=sys.stderr)
        if any(p.endswith(": unknown key") for p in problems):
            print("drover: a removed key (truth_gate, roles.review.second_opinion, ...)? run `drover setup fix` to drop it",
                  file=sys.stderr)
        return 65 if problems else 0
    elif cmd == "harness":
        print(json.dumps(harness(argv[1] if len(argv) > 1 else configured_harness()), indent=2))
    elif cmd == "worker-argv" and len(argv) in (3, 4):
        model = argv[3] if len(argv) == 4 else None
        sys.stdout.write("".join(a + "\0" for a in worker_argv(argv[1], argv[2], model=model)))
    elif cmd == "model" and len(argv) >= 2:
        rest, builder = argv[2:], None
        if "--builder" in rest:
            i = rest.index("--builder")
            builder = rest[i + 1] if i + 1 < len(rest) else None
            rest = rest[:i] + rest[i + 2:]
        print(role_model(load_config(), argv[1], rest[0] if rest else None, builder))
    elif cmd == "skills-install":
        def opt(flag):
            if flag not in argv:
                return None
            i = argv.index(flag)
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                print(f"drover skills install: {flag} needs a value", file=sys.stderr)
                raise SystemExit(64)
            return argv[i + 1]
        dest, name = opt("--dest"), opt("--harness")
        return skills_install(dest, name)
    else:
        print(__doc__, file=sys.stderr)
        return 64
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
