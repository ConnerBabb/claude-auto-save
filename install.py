#!/usr/bin/env python3
"""
Installer for claude-auto-save.

Copies the context-headroom hook + skill files into ~/.claude/, then
merges the UserPromptSubmit hook entry into settings.json without
clobbering existing settings. Idempotent — re-running won't duplicate.

Resolves the absolute path of the running Python interpreter and the
installed script, writing them straight into settings.json. No env
vars or path expansion in the config file — dodges hook-command parser
quirks across Claude Code versions.

Usage:
    python install.py              # standard install
    python install.py --force      # overwrite existing skill files
    python install.py --dry-run    # show what would happen, no changes
    python install.py --uninstall  # remove hook entry and files
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HOOK_SCRIPT = "check-context-headroom.py"
# Other Python helpers that ship next to the hook script and are
# invoked by the /save-context skill at runtime.
COMPANION_SCRIPTS = ("memory_write.py",)
# Substring used to match existing hook entries pointing at any prior
# version of this script (e.g., a legacy PowerShell .ps1 install).
HOOK_MATCH_TOKEN = "check-context-headroom"
SKILL_FILES = ("save-context.md", "skip-save.md")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install claude-auto-save")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing skill files instead of skipping")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen; make no changes")
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove hook registration and installed files")
    args = parser.parse_args()

    paths = _resolve_paths()

    if args.uninstall:
        return _uninstall(paths, dry_run=args.dry_run)

    return _install(paths, force=args.force, dry_run=args.dry_run)


def _detect_claude_code() -> tuple[bool, str | None]:
    """Try `claude --version`. Returns (found, version_string).
    Non-fatal: returns (False, None) if the binary isn't on PATH or
    exits non-zero. Output format observed: "2.1.148 (Claude Code)".

    Uses shutil.which() so PATHEXT is honored on Windows (npm installs
    claude.cmd alongside claude.ps1 — subprocess can call the .cmd
    directly, but not the .ps1).
    """
    claude = shutil.which("claude")
    if not claude:
        return False, None
    try:
        result = subprocess.run(
            [claude, "--version"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False, None
    if result.returncode != 0:
        return False, None
    return True, result.stdout.strip() or None


def _resolve_paths() -> dict:
    src_dir = Path(__file__).resolve().parent
    home = Path.home()
    claude_dir = home / ".claude"
    return {
        "src_dir": src_dir,
        "claude_dir": claude_dir,
        "hooks_dir": claude_dir / "hooks",
        "commands_dir": claude_dir / "commands",
        "settings": claude_dir / "settings.json",
        "hook_script_src": src_dir / HOOK_SCRIPT,
        "hook_script_dst": claude_dir / "hooks" / HOOK_SCRIPT,
    }


def _install(paths: dict, force: bool, dry_run: bool) -> int:
    if not paths["hook_script_src"].is_file():
        print(f"error: {paths['hook_script_src']} not found", file=sys.stderr)
        return 1

    prefix = "would " if dry_run else ""
    print(f"{'(dry-run) ' if dry_run else ''}Installing claude-auto-save")
    print(f"  target: {paths['claude_dir']}")

    # Detect Claude Code (informational; install proceeds either way).
    found, version = _detect_claude_code()
    if found:
        print(f"  detected: {version}")
    else:
        print("  warn:    'claude' binary not on PATH - install proceeds, but the hook")
        print("           won't fire until Claude Code is installed and PATH is updated.")
    print()

    # 1. Create directories
    for d in (paths["hooks_dir"], paths["commands_dir"]):
        if not d.exists():
            print(f"  {prefix}create {d}")
            if not dry_run:
                d.mkdir(parents=True, exist_ok=True)

    # 2. Copy hook script and companion helpers
    print(f"  {prefix}copy   {HOOK_SCRIPT}  ->  {paths['hook_script_dst']}")
    if not dry_run:
        shutil.copy2(paths["hook_script_src"], paths["hook_script_dst"])

    for companion in COMPANION_SCRIPTS:
        src = paths["src_dir"] / companion
        if not src.is_file():
            print(f"  -      skip   {companion}  (not in package)")
            continue
        dst = paths["hooks_dir"] / companion
        print(f"  {prefix}copy   {companion}  ->  {dst}")
        if not dry_run:
            shutil.copy2(src, dst)

    # 3. Copy skill files (skip existing unless --force)
    for name in SKILL_FILES:
        src = paths["src_dir"] / "commands" / name
        if not src.is_file():
            print(f"  -      skip   {name}  (not in package)")
            continue
        dst = paths["commands_dir"] / name
        if dst.exists() and not force:
            print(f"  -      skip   {name}  (already at {dst}; pass --force to overwrite)")
            continue
        print(f"  {prefix}copy   {name}  ->  {dst}")
        if not dry_run:
            shutil.copy2(src, dst)

    # 4. Build the hook command with fully resolved absolute paths.
    python_exe = sys.executable.replace("\\", "/")
    script_path = str(paths["hook_script_dst"]).replace("\\", "/")
    hook_command = _format_command(python_exe, script_path)

    # 5. Merge into settings.json
    config, status = _merge_settings(paths["settings"], hook_command, dry_run=dry_run)
    print(f"  {prefix}{status}")

    print()
    print(f"{'(dry-run) ' if dry_run else ''}Done.")
    if dry_run:
        return 0

    _print_next_steps(paths["settings"])
    return 0


def _format_command(python_exe: str, script_path: str) -> str:
    needs_quoting = " " in python_exe or " " in script_path
    if needs_quoting:
        return f'"{python_exe}" "{script_path}"'
    return f"{python_exe} {script_path}"


def _merge_settings(settings_path: Path, hook_command: str, dry_run: bool) -> tuple[dict, str]:
    """Load settings.json (or start fresh), inject the hook entry idempotently,
    and write back. Returns (config, status_message)."""
    if settings_path.exists():
        try:
            config = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"error: {settings_path} is invalid JSON: {e}")
    else:
        config = {}

    hooks_section = config.setdefault("hooks", {})
    user_prompt_hooks = hooks_section.setdefault("UserPromptSubmit", [])

    existing = _find_existing_entry(user_prompt_hooks)
    if existing is not None:
        if existing["command"] == hook_command:
            return config, "settings.json already has hook registered (no change)"
        status = "update existing hook command in settings.json"
        if not dry_run:
            existing["command"] = hook_command
            settings_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return config, status

    new_entry = {
        "matcher": "*",
        "hooks": [{"type": "command", "command": hook_command}],
    }
    status = "register hook in settings.json"
    if not dry_run:
        user_prompt_hooks.append(new_entry)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config, status


def _find_existing_entry(user_prompt_hooks: list) -> dict | None:
    """Return the inner hook dict that references our script, if any.
    We match on filename substring so re-installs from different paths
    still update in place rather than duplicating."""
    for entry in user_prompt_hooks:
        for h in entry.get("hooks", []):
            if HOOK_MATCH_TOKEN in h.get("command", ""):
                return h
    return None


def _uninstall(paths: dict, dry_run: bool) -> int:
    prefix = "would " if dry_run else ""
    print(f"{'(dry-run) ' if dry_run else ''}Uninstalling claude-auto-save")
    print()

    # 1. Remove hook entry from settings.json
    if paths["settings"].exists():
        try:
            config = json.loads(paths["settings"].read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  warn: cannot parse {paths['settings']}; skipping hook removal")
            config = None

        if config is not None:
            user_prompt_hooks = config.get("hooks", {}).get("UserPromptSubmit", [])
            removed_any = False
            for entry in list(user_prompt_hooks):
                entry["hooks"] = [h for h in entry.get("hooks", [])
                                  if HOOK_SCRIPT not in h.get("command", "")]
                if not entry["hooks"]:
                    user_prompt_hooks.remove(entry)
                    removed_any = True
                else:
                    removed_any = True
            if removed_any:
                print(f"  {prefix}remove hook entry from settings.json")
                if not dry_run:
                    paths["settings"].write_text(json.dumps(config, indent=2), encoding="utf-8")

    # 2. Remove hook script and companions
    if paths["hook_script_dst"].exists():
        print(f"  {prefix}delete {paths['hook_script_dst']}")
        if not dry_run:
            paths["hook_script_dst"].unlink()

    for companion in COMPANION_SCRIPTS:
        dst = paths["hooks_dir"] / companion
        if dst.exists():
            print(f"  {prefix}delete {dst}")
            if not dry_run:
                dst.unlink()

    # 3. Skill files are user-editable; do NOT auto-delete.
    print(f"  -      skill files left in place ({paths['commands_dir']}) - remove manually if desired")

    # 4. Optionally clean up sentinels next to old hook
    sentinel_dir = paths["hook_script_dst"].parent / ".sentinels"
    if sentinel_dir.exists():
        print(f"  {prefix}remove {sentinel_dir}")
        if not dry_run:
            shutil.rmtree(sentinel_dir, ignore_errors=True)

    print()
    print(f"{'(dry-run) ' if dry_run else ''}Done.")
    return 0


def _print_next_steps(settings_path: Path) -> None:
    print()
    print("Optional next steps:")
    print()
    print("  1. The hook defaults to a 1M context window (matches current")
    print("     Opus). If you want it to detect the window size automatically")
    print(f"     instead, edit the 'env' block in {settings_path}:")
    print('        "env": {')
    print('          "CLAUDE_HOOK_CONTEXT_LIMIT": "auto"')
    print('        }')
    print("     Or set an explicit integer for any other model size.")
    print()
    print("  2. Adjust the trigger threshold (default: fire at <15000 tokens")
    print("     of headroom remaining). Add to the same 'env' block:")
    print('        "CLAUDE_HOOK_THRESHOLD": "30000"')
    print()
    print("  The hook fires once per session via a sentinel file; delete")
    print("  ~/.claude/hooks/.sentinels/<session_id>.flag to re-fire.")


if __name__ == "__main__":
    sys.exit(main())
