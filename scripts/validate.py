"""Agent Plugins 1.0.0 conformance checker (stdlib only, no dependencies).

Validates this repository as an Agent Plugins 1.0.0 package:
- plugin.json closed manifest, required fields, and name constraints (§5.2, §5.5)
- mcp.json closed config, server variants, and schema-version match (§7.2.1, §10.1)
- skills/ discovery layout and SKILL.md frontmatter presence (§6.1, §7.1)

Exit code 0 = conformant; non-zero = violations reported on stderr.
The normative specification text is authoritative over the JSON Schemas.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"

# §5.2 closed manifest: the only permitted top-level fields.
MANIFEST_FIELDS = {
    "$schema", "name", "version", "description", "author",
    "homepage", "repository", "license", "keywords", "extensions",
}
# §5.4 author object allows only name/email/url.
AUTHOR_FIELDS = {"name", "email", "url"}
# §5.5 plugin name constraints: 1-64 chars, [a-z0-9.-], no '--'/'..',
# alphanumeric start/end.
NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
# §7.2.1 stdio variant fields.
STDIO_FIELDS = {"type", "command", "args", "env", "cwd"}
# §7.2.1 streamable-http / sse variant fields.
HTTP_FIELDS = {"type", "url", "headers"}
# §7.2.1 cwd must be './...', '${PLUGIN_ROOT}[/...]', or '${PLUGIN_DATA}[/...]'.
CWD_RE = re.compile(r"^(?:\./|\$\{PLUGIN_ROOT\}(?:/|$)|\$\{PLUGIN_DATA\}(?:/|$))")
# §9.1 reserved env entries.
RESERVED_ENV = {"PLUGIN_ROOT", "PLUGIN_DATA"}

errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def load_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        fail(f"{path.name}: invalid JSON: {exc}")
        return None
    if not isinstance(data, dict):
        fail(f"{path.name}: top-level value must be an object")
        return None
    return data


def validate_plugin(data: dict) -> None:
    unknown = set(data) - MANIFEST_FIELDS
    if unknown:
        fail(f"plugin.json: unknown top-level field(s) {sorted(unknown)}; "
             f"client-specific data belongs under 'extensions' (§5.2)")

    schema = data.get("$schema")
    if schema != PLUGIN_SCHEMA:
        fail(f"plugin.json: '$schema' must be {PLUGIN_SCHEMA!r}, got {schema!r} (§5.2)")
    name = data.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 64) or not NAME_RE.match(name):
        fail(f"plugin.json: 'name' {name!r} violates §5.5 constraints")
    for field in ("version", "description", "homepage", "repository", "license"):
        if field in data and not isinstance(data[field], str):
            fail(f"plugin.json: '{field}' must be a string")
    if "keywords" in data and (
        not isinstance(data["keywords"], list)
        or not all(isinstance(k, str) for k in data["keywords"])
    ):
        fail("plugin.json: 'keywords' must be an array of strings")
    if "author" in data:
        author = data["author"]
        if not isinstance(author, dict) or not set(author) <= AUTHOR_FIELDS:
            fail(f"plugin.json: 'author' must be an object with only {sorted(AUTHOR_FIELDS)}")
        elif not all(isinstance(v, str) for v in author.values()):
            fail("plugin.json: 'author' field values must be strings")
    if "extensions" in data and (
        not isinstance(data["extensions"], dict)
        or not all(isinstance(v, dict) for v in data["extensions"].values())
    ):
        fail("plugin.json: 'extensions' must be an object of objects (§8.1)")


def schema_version(uri: str | None) -> str:
    """Extract the Agent Plugins version segment from a canonical schema URL."""
    return uri.rsplit("/", 2)[-2] if uri else ""


def validate_mcp(data: dict, plugin_schema: str) -> None:
    schema = data.get("$schema")
    if schema != MCP_SCHEMA:
        fail(f"mcp.json: '$schema' must be {MCP_SCHEMA!r}, got {schema!r} (§7.2.1)")
    elif schema_version(schema) != schema_version(plugin_schema):
        fail(f"mcp.json: '$schema' version must match plugin.json's (§10.1): "
             f"{schema_version(schema)!r} != {schema_version(plugin_schema)!r}")
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        fail("mcp.json: 'mcpServers' must be an object (§7.2.1)")
        return
    for server_name, server in servers.items():
        if not isinstance(server, dict):
            fail(f"mcp.json: server {server_name!r} must be an object")
            continue
        server_type = server.get("type")
        if server_type not in ("stdio", "streamable-http", "sse"):
            fail(f"mcp.json: server {server_name!r} has unknown 'type' {server_type!r} (§7.2.1)")
            continue
        variant_fields = STDIO_FIELDS if server_type == "stdio" else HTTP_FIELDS
        unknown = set(server) - variant_fields
        if unknown:
            fail(f"mcp.json: server {server_name!r} has field(s) {sorted(unknown)} "
                 f"not valid for '{server_type}' variant (§7.2.1)")
        if server_type == "stdio":
            command = server.get("command")
            if not isinstance(command, str) or not command:
                fail(f"mcp.json: server {server_name!r} requires a non-empty 'command' (§7.2.1)")
            args = server.get("args", [])
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                fail(f"mcp.json: server {server_name!r} 'args' must be an array of strings")
            cwd = server.get("cwd")
            if cwd is not None and not (isinstance(cwd, str) and CWD_RE.match(cwd)):
                fail(f"mcp.json: server {server_name!r} 'cwd' {cwd!r} must be './', "
                     "'${PLUGIN_ROOT}/...', or '${PLUGIN_DATA}/...' (§7.2.1)")
            env = server.get("env", {})
            if not isinstance(env, dict):
                fail(f"mcp.json: server {server_name!r} 'env' must be an object")
            else:
                if not all(isinstance(v, str) for v in env.values()):
                    fail(f"mcp.json: server {server_name!r} 'env' values must be strings")
                if RESERVED_ENV & set(env):
                    fail(f"mcp.json: server {server_name!r} 'env' must not set "
                         f"{sorted(RESERVED_ENV & set(env))} (§9.1)")
        else:  # streamable-http / sse
            url = server.get("url")
            if not isinstance(url, str) or not re.match(r"^https?://", url):
                fail(f"mcp.json: server {server_name!r} 'url' must be an absolute "
                     "http(s) URL (§7.2.1)")
            headers = server.get("headers", {})
            if not isinstance(headers, dict) or not all(
                isinstance(v, str) for v in headers.values()
            ):
                fail(f"mcp.json: server {server_name!r} 'headers' must be an object of strings")
            elif len({h.lower() for h in headers}) != len(headers):
                fail(f"mcp.json: server {server_name!r} 'headers' has case-insensitive "
                     "duplicate names (§7.2.1)")


def validate_skills() -> None:
    skills_dir = ROOT / "skills"
    if not skills_dir.is_dir():
        fail("skills/: fixed location missing (optional per §6.2, but declared in this plugin)")
        return
    # §7.1: each immediate child dir with a regular SKILL.md is one skill; no recursion.
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            fail(f"skills/{child.name}: directory without SKILL.md is not a skill (§7.1)")
            continue
        frontmatter = parse_frontmatter(skill_md)
        for required in ("name", "description"):
            if required not in frontmatter:
                fail(f"skills/{child.name}/SKILL.md: missing required frontmatter "
                     f"'{required}' (Agent Skills spec)")
        if frontmatter.get("name") != child.name:
            fail(f"skills/{child.name}/SKILL.md: frontmatter 'name' "
                 f"{frontmatter.get('name')!r} must equal the skill directory name (§7.1)")


def parse_frontmatter(path: Path) -> dict[str, str]:
    """YAML-lite frontmatter reader: `key: value` lines between --- delimiters."""
    text = path.read_text(encoding="utf-8")
    fields: dict[str, str] = {}
    if not text.startswith("---"):
        return fields
    lines = text.splitlines()[1:]
    for line in lines:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def main() -> int:
    plugin = load_json(ROOT / "plugin.json")
    if plugin is not None:
        validate_plugin(plugin)
    mcp = load_json(ROOT / "mcp.json")
    if mcp is not None:
        validate_mcp(mcp, plugin.get("$schema", "") if plugin else "")
    validate_skills()

    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        print(f"{len(errors)} conformance violation(s) against Agent Plugins 1.0.0", file=sys.stderr)
        return 1
    print("OK: repository conforms to Agent Plugins 1.0.0 "
          "(plugin.json, mcp.json, skills/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
