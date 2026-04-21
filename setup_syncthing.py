#!/usr/bin/env python3
"""Configure Syncthing to sync a Claude or Codex state handoff folder.

This script intentionally edits Syncthing's on-disk config.xml. It refuses to
touch the config while the local GUI/API port appears reachable, because
Syncthing may overwrite manual edits while running.
"""

from __future__ import annotations

import argparse
import copy
import shutil
import socket
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

CLAUDE_STIGNORE_CONTENT = """(?d).DS_Store
(?d)Thumbs.db
(?d)._*
(?d)**/*.swp
(?d)**/*~
(?d)**/*.tmp
(?d)**/*.temp

!/projects
!/projects/**
!/plans
!/plans/**
!/file-history
!/file-history/**
!/sessions
!/sessions/**
!/history.jsonl

**
"""

CODEX_STIGNORE_CONTENT = """(?d).DS_Store
(?d)Thumbs.db
(?d)._*
(?d)**/*.swp
(?d)**/*~
(?d)**/*.tmp
(?d)**/*.temp

!/sessions
!/sessions/**
!/archived_sessions
!/archived_sessions/**

**
"""


@dataclass(frozen=True)
class SyncthingDevice:
    device_id: str
    name: str


@dataclass(frozen=True)
class AppProfile:
    name: str
    state_dir: Path
    folder_id: str
    label: str
    stignore_content: str


APP_PROFILES = {
    "claude": AppProfile(
        name="Claude",
        state_dir=Path.home() / ".claude",
        folder_id="claude-state",
        label="claude-state",
        stignore_content=CLAUDE_STIGNORE_CONTENT,
    ),
    "codex": AppProfile(
        name="Codex",
        state_dir=Path.home() / ".codex",
        folder_id="codex-state",
        label="codex-state",
        stignore_content=CODEX_STIGNORE_CONTENT,
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add or update a Claude or Codex Syncthing folder plus .stignore."
    )
    parser.add_argument(
        "--app",
        choices=sorted(APP_PROFILES),
        default="claude",
        help="State profile to configure (default: claude).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to Syncthing config.xml. Auto-detected if omitted.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="State directory to sync. Defaults to the selected app profile.",
    )
    parser.add_argument(
        "--folder-id",
        default=None,
        help="Syncthing folder ID. Defaults to the selected app profile.",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Syncthing folder label. Defaults to the selected app profile.",
    )
    parser.add_argument(
        "--device-id",
        action="append",
        default=[],
        help="Remote device ID to share with. Repeat as needed.",
    )
    parser.add_argument(
        "--all-configured-devices",
        action="store_true",
        help="Share with every device already configured in Syncthing.",
    )
    parser.add_argument(
        "--no-share",
        action="store_true",
        help="Create the folder locally without sharing it to any remote devices.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Print configured remote devices and exit.",
    )
    parser.add_argument(
        "--force-ignore",
        action="store_true",
        help="Overwrite an existing .stignore even if it differs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned changes without writing files.",
    )
    return parser.parse_args()


def find_config_path(explicit: Path | None) -> Path:
    if explicit:
        return explicit.expanduser().resolve()

    candidates = [
        Path.home() / "Library/Application Support/Syncthing/config.xml",
        Path.home() / ".local/state/syncthing/config.xml",
        Path.home() / ".config/syncthing/config.xml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Could not find Syncthing config.xml. Pass it explicitly with --config."
    )


def load_config(config_path: Path) -> ET.ElementTree:
    try:
        return ET.parse(config_path)
    except ET.ParseError as exc:
        raise SystemExit(f"Failed to parse {config_path}: {exc}") from exc


def configured_devices(root: ET.Element) -> list[SyncthingDevice]:
    devices = []
    for device_el in root.findall("device"):
        device_id = device_el.get("id", "").strip()
        if not device_id:
            continue
        devices.append(
            SyncthingDevice(
                device_id=device_id,
                name=device_el.get("name", "").strip() or "(unnamed)",
            )
        )
    return devices


def choose_devices(args: argparse.Namespace, devices: list[SyncthingDevice]) -> list[str]:
    if args.no_share:
        return []

    if args.device_id:
        known_ids = {device.device_id for device in devices}
        unknown = [device_id for device_id in args.device_id if device_id not in known_ids]
        if unknown:
            joined = ", ".join(unknown)
            raise SystemExit(f"Unknown device ID(s): {joined}. Use --list-devices first.")
        return args.device_id

    if args.all_configured_devices:
        return [device.device_id for device in devices]

    return [device.device_id for device in devices]


def gui_address(root: ET.Element) -> tuple[str, int] | None:
    gui = root.find("gui")
    if gui is None:
        return None

    address = (gui.findtext("address") or "").strip()
    if not address:
        return None

    if address.startswith("unix://"):
        return None

    if ":" not in address:
        return None

    host, port_str = address.rsplit(":", 1)
    try:
        return host, int(port_str)
    except ValueError:
        return None


def is_gui_reachable(address: tuple[str, int] | None) -> bool:
    if address is None:
        return False
    host, port = address
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def build_folder_element(
    root: ET.Element,
    folder_id: str,
    label: str,
    path: Path,
    device_ids: list[str],
) -> ET.Element:
    defaults = root.find("defaults/folder")
    if defaults is not None:
        folder = copy.deepcopy(defaults)
    else:
        folder = ET.Element(
            "folder",
            {
                "id": "",
                "label": "",
                "path": "",
                "type": "sendreceive",
                "rescanIntervalS": "3600",
                "fsWatcherEnabled": "true",
                "fsWatcherDelayS": "10",
                "fsWatcherTimeoutS": "0",
                "ignorePerms": "false",
                "autoNormalize": "true",
            },
        )
        ET.SubElement(folder, "filesystemType").text = "basic"
        min_disk = ET.SubElement(folder, "minDiskFree", {"unit": "%"})
        min_disk.text = "1"

    folder.set("id", folder_id)
    folder.set("label", label)
    folder.set("path", str(path))
    folder.set("type", "sendreceive")
    folder.set("paused", "false")

    remove_folder_devices(folder)
    insert_folder_devices(folder, device_ids)
    return folder


def remove_folder_devices(folder: ET.Element) -> None:
    for child in list(folder):
        if child.tag == "device":
            folder.remove(child)


def insert_folder_devices(folder: ET.Element, device_ids: list[str]) -> None:
    children = list(folder)
    insert_at = 0
    for idx, child in enumerate(children):
        if child.tag in {"filesystemType", "path"}:
            insert_at = idx + 1
        if child.tag == "minDiskFree":
            insert_at = idx
            break

    for device_id in device_ids:
        device_el = ET.Element("device", {"id": device_id, "introducedBy": ""})
        ET.SubElement(device_el, "encryptionPassword").text = ""
        folder.insert(insert_at, device_el)
        insert_at += 1


def upsert_folder(root: ET.Element, folder: ET.Element, folder_id: str) -> str:
    existing = None
    for folder_el in root.findall("folder"):
        if folder_el.get("id") == folder_id:
            existing = folder_el
            break

    if existing is not None:
        root.remove(existing)
        insert_folder_at_top(root, folder)
        return "updated"

    insert_folder_at_top(root, folder)
    return "created"


def insert_folder_at_top(root: ET.Element, folder: ET.Element) -> None:
    insert_idx = len(root)
    for idx, child in enumerate(list(root)):
        if child.tag != "folder":
            insert_idx = idx
            break
    root.insert(insert_idx, folder)


def ensure_ignore_file(
    state_dir: Path,
    stignore_content: str,
    force_ignore: bool,
    dry_run: bool,
) -> tuple[Path, str]:
    ignore_path = state_dir / ".stignore"
    status = "unchanged"

    if ignore_path.exists():
        current = ignore_path.read_text(encoding="utf-8")
        if current == stignore_content:
            return ignore_path, status
        if not force_ignore:
            raise SystemExit(
                f"{ignore_path} already exists and differs. Re-run with --force-ignore."
            )
        status = "updated"
    else:
        status = "created"

    if not dry_run:
        ignore_path.write_text(stignore_content, encoding="utf-8")

    return ignore_path, status


def backup_path_for(config_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return config_path.with_name(f"{config_path.name}.bak.{stamp}")


def write_config(tree: ET.ElementTree, config_path: Path, dry_run: bool) -> Path | None:
    backup_path = backup_path_for(config_path)
    if dry_run:
        return backup_path

    shutil.copy2(config_path, backup_path)
    ET.indent(tree, space="    ")
    tree.write(config_path, encoding="utf-8", xml_declaration=False)
    return backup_path


def print_devices(devices: list[SyncthingDevice]) -> None:
    if not devices:
        print("No configured Syncthing remote devices found.")
        return

    print("Configured Syncthing remote devices:")
    for device in devices:
        print(f"- {device.name}: {device.device_id}")


def main() -> int:
    args = parse_args()
    profile = APP_PROFILES[args.app]
    config_path = find_config_path(args.config)
    state_dir = (args.state_dir or profile.state_dir).expanduser().resolve()
    tree = load_config(config_path)
    root = tree.getroot()
    devices = configured_devices(root)

    if args.list_devices:
        print_devices(devices)
        return 0

    if not state_dir.exists():
        raise SystemExit(f"{profile.name} directory does not exist: {state_dir}")

    reachable = is_gui_reachable(gui_address(root))
    if reachable:
        raise SystemExit(
            "Syncthing GUI/API appears reachable. Stop Syncthing before editing "
            "config.xml with this script."
        )

    target_devices = choose_devices(args, devices)
    folder = build_folder_element(
        root=root,
        folder_id=args.folder_id or profile.folder_id,
        label=args.label or profile.label,
        path=state_dir,
        device_ids=target_devices,
    )
    folder_id = args.folder_id or profile.folder_id
    folder_status = upsert_folder(root, folder, folder_id)
    ignore_path, ignore_status = ensure_ignore_file(
        state_dir=state_dir,
        stignore_content=profile.stignore_content,
        force_ignore=args.force_ignore,
        dry_run=args.dry_run,
    )
    backup_path = write_config(tree, config_path, args.dry_run)

    configured = {device.device_id: device.name for device in devices}
    shared_with = [
        f"{configured.get(device_id, '(unknown)')} ({device_id})"
        for device_id in target_devices
    ]

    print(f"Config file: {config_path}")
    print(f"App profile: {profile.name}")
    print(f"State dir: {state_dir}")
    print(f"Folder {folder_id!r}: {folder_status}")
    print(f"Ignore file {ignore_path}: {ignore_status}")
    if backup_path is not None:
        print(f"Config backup: {backup_path}")

    if shared_with:
        print("Shared with:")
        for item in shared_with:
            print(f"- {item}")
    else:
        print("Shared with: no remote devices")

    if args.dry_run:
        print("Dry run only. No files were written.")
    else:
        print("Restart Syncthing after this change.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
