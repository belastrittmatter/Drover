#!/usr/bin/env python3
"""drover-config.py — the project config, its schema and the harness entries.

    drover config path                  print the config path (<main checkout>/.drover/config.json)
    drover config home                  print the drover install folder (workflows/, skills/, hooks/)
    drover config slug                  print the run slug: the repo name (FR-CFG-1, FR-MET-1)
    drover config validate [FILE]       validate against config-schema.json (FR-CFG-2)
    drover config harness [NAME]        print harnesses/<NAME>.json (default: the config's harness)
    drover config worker-argv ROLE PROMPT   NUL-separated argv of one headless worker (the runner)
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
    return common.parent if common.name == ".git" else common


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
    `//` starts an absolute path in a Claude permission rule."""
    perms = entry.get("permissions", {})
    dirs = [d.replace("{drover_home}", str(HOME)) for d in perms.get("read_only_dirs", [])]
    return ([f"Read(//{d.lstrip('/')}/**)" for d in dirs],
            [f"{tool}(//{d.lstrip('/')}/**)" for d in dirs for tool in perms.get("read_only_deny", [])])


def allowed(entry, cfg):
    """The permission rules a headless worker starts with (FR-HRN-4): the harness's tools,
    one Bash rule per command word, the first word of each project verify command, and Read
    on the read-only folders. The allow list is broad on purpose: hooks hold the boundary."""
    perms = entry.get("permissions", {})
    words = list(perms.get("commands", []))
    for cmd in cfg.get("verify_commands") or []:
        for part in re.split(r"&&|\|\||;|\|", cmd):
            first = (part.split() or [""])[0]
            if first and first not in words and re.fullmatch(r"[A-Za-z0-9._/-]+", first):
                words.append(first)
    return list(perms.get("tools", [])) + [f"Bash({w}:*)" for w in words] + read_only(entry)[0]


def worker_argv(role, prompt, cwd=None):
    """argv for one headless worker of `role`: the harness command, the prompt, the role's
    model and the permissions. The runner reads it NUL-separated."""
    p = config_path(cwd)
    cfg = json.loads(p.read_text()) if p.is_file() else {}
    rcfg = (cfg.get("roles") or {}).get(role) or {}
    entry = harness(rcfg.get("harness") or cfg.get("harness") or "claude")
    argv = list(entry["worker_command"]) + [prompt]
    if rcfg.get("model") and entry.get("model_flag"):
        argv += [entry["model_flag"], rcfg["model"]]
    perms = entry.get("permissions", {})
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
        return 65 if problems else 0
    elif cmd == "harness":
        print(json.dumps(harness(argv[1] if len(argv) > 1 else configured_harness()), indent=2))
    elif cmd == "worker-argv" and len(argv) == 3:
        sys.stdout.write("".join(a + "\0" for a in worker_argv(argv[1], argv[2])))
    elif cmd == "skills-install":
        dest = argv[argv.index("--dest") + 1] if "--dest" in argv else None
        name = argv[argv.index("--harness") + 1] if "--harness" in argv else None
        return skills_install(dest, name)
    else:
        print(__doc__, file=sys.stderr)
        return 64
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
