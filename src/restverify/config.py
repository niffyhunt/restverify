"""Configuration and zero-config resolution (R1, R2, R3, R5, R16, R24).

U1 (zero-config-first): a missing config file is NOT an error. `run -r <repo>`
is a complete first experience; the config exists to add extra repos, sources
and excludes for people who want them.

R2/R24: this module never stores a password. The only credential-shaped field
is `password_command`, which names a command the user already trusts (the same
contract restic itself offers via RESTIC_PASSWORD_COMMAND).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

CONFIG_ENV = "RESTVERIFY_CONFIG"
DEFAULT_SNAPSHOT = "latest"


def default_config_path() -> Path:
    """R16: `~/.config/restverify/config.toml` (XDG-aware, env-overridable)."""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "restverify" / "config.toml"


@dataclass
class RepoEntry:
    """One repository to verify. `repo` is an opaque restic location (N3)."""

    name: str
    repo: str
    source: str | None = None
    excludes: list[str] = field(default_factory=list)
    snapshot: str = DEFAULT_SNAPSHOT
    password_command: str | None = None
    strict: bool = False
    no_source: bool = False


@dataclass
class Config:
    repos: list[RepoEntry] = field(default_factory=list)
    path: Path | None = None

    def find(self, needle: str | None) -> RepoEntry | None:
        """Match a repo by configured name or by its repo string."""
        if not needle:
            return None
        for entry in self.repos:
            if needle in (entry.name, entry.repo):
                return entry
        return None


def _entry_from_table(table: dict, where: str) -> RepoEntry:
    repo = table.get("repo")
    if not isinstance(repo, str) or not repo.strip():
        raise ConfigError(
            f"{where}: every [[repo]] needs a non-empty 'repo' (the restic location).",
            hint="add repo = \"/path/to/repo\" or run `restverify init`",
        )
    # R2/R24 enforcement: a stored password is refused outright, never ignored.
    # Ignoring it would let a user believe a secret is in use while leaving the
    # plaintext sitting in a world-readable config.
    for banned in ("password", "restic_password"):
        if banned in table:
            raise ConfigError(
                f"{where}: '{banned}' is not supported - restverify never stores "
                "passwords, and silently ignoring one would leave your secret on disk.",
                hint="delete that line and use password_command instead, e.g. "
                     'password_command = "pass show restic/srv"',
            )
    name = table.get("name") or Path(repo.rstrip("/")).name or repo
    excludes = table.get("excludes") or []
    if not isinstance(excludes, list) or any(not isinstance(x, str) for x in excludes):
        raise ConfigError(
            f"{where}: 'excludes' must be a list of strings.",
            hint='write excludes = ["*.log", "cache/"]',
        )
    return RepoEntry(
        name=str(name),
        repo=repo,
        source=table.get("source") or None,
        excludes=list(excludes),
        snapshot=str(table.get("snapshot") or DEFAULT_SNAPSHOT),
        password_command=table.get("password_command") or None,
        strict=bool(table.get("strict", False)),
        no_source=bool(table.get("no_source", False)),
    )


def load_config(path: Path | None = None) -> Config:
    """Load config; a missing file yields an empty Config (U1), not an error."""
    target = Path(path).expanduser() if path else default_config_path()
    if not target.exists():
        return Config(repos=[], path=target)
    try:
        with target.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{target} is not valid TOML: {exc}",
            hint=f"fix the syntax, or move it aside and run `restverify init`",
        ) from exc
    except OSError as exc:
        raise ConfigError(
            f"could not read {target}: {exc.strerror or exc}",
            hint=f"check permissions on {target}",
        ) from exc

    tables = data.get("repo") or []
    if not isinstance(tables, list):
        raise ConfigError(
            f"{target}: expected [[repo]] tables, found a single value.",
            hint='use [[repo]] (double brackets) for each repository',
        )
    repos = [_entry_from_table(t, f"{target} (repo #{i + 1})")
             for i, t in enumerate(tables) if isinstance(t, dict)]
    return Config(repos=repos, path=target)


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_config(config: Config) -> str:
    """Serialise to TOML. tomllib is read-only, so we emit our own schema.

    Deliberately tiny: strings, booleans and string arrays only. Anything richer
    would be scope creep for a config this small.
    """
    lines = [
        "# restverify configuration",
        "# Passwords are NEVER stored here. To use one, set password_command to a",
        "# command that prints it (e.g. password_command = \"pass show restic/srv\").",
        "",
    ]
    for entry in config.repos:
        lines.append("[[repo]]")
        lines.append(f"name = {_toml_str(entry.name)}")
        lines.append(f"repo = {_toml_str(entry.repo)}")
        if entry.source:
            lines.append(f"source = {_toml_str(entry.source)}")
        if entry.snapshot:
            lines.append(f"snapshot = {_toml_str(entry.snapshot)}")
        if entry.password_command:
            lines.append(f"password_command = {_toml_str(entry.password_command)}")
        if entry.excludes:
            items = ", ".join(_toml_str(x) for x in entry.excludes)
            lines.append(f"excludes = [{items}]")
        if entry.strict:
            lines.append("strict = true")
        if entry.no_source:
            lines.append("no_source = true")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def save_config(config: Config, path: Path | None = None) -> Path:
    target = Path(path).expanduser() if path else (config.path or default_config_path())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_config(config), encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"could not write {target}: {exc.strerror or exc}",
            hint="check the directory exists and is writable",
        ) from exc
    return target
