#!/usr/bin/env python3
"""Ensure the V2G Liberty package include is present in HA's configuration.yaml.

This script is called during add-on startup (s6-overlay oneshot) to automatically
add the required package include line to Home Assistant's configuration.yaml.

It uses text-based manipulation (not YAML parse/dump) to preserve the user's
file formatting, comments, and !include directives exactly as-is.
"""

import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request, urlopen

# Constants
CONFIG_FILE = "/homeassistant/configuration.yaml"
V2G_PACK_LINE = "    v2g_pack: !include packages/v2g_liberty/v2g_liberty_package.yaml"
V2G_PACK_IDENTIFIER = "v2g_pack:"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_API = "http://supervisor/core/api"

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] ensure_ha_package_include: %(message)s",
)
log = logging.getLogger(__name__)


def send_persistent_notification(title: str, message: str, notification_id: str):
    """Send a persistent notification to HA via the Supervisor API."""
    if not SUPERVISOR_TOKEN:
        log.warning("No SUPERVISOR_TOKEN available, cannot send notification.")
        return

    url = f"{SUPERVISOR_API}/services/persistent_notification/create"
    payload = json.dumps(
        {
            "title": title,
            "message": message,
            "notification_id": notification_id,
        }
    ).encode("utf-8")

    req = Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {SUPERVISOR_TOKEN}")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                log.info("Persistent notification sent successfully.")
            else:
                log.warning(f"Notification response status: {resp.status}")
    except URLError as e:
        log.warning(f"Could not send persistent notification: {e}")


def write_file_atomic(path: str, content: str):
    """Write content to a file atomically via temp file + rename.

    This avoids the truncate-then-write pattern of open("w") which can
    leave stale bytes on Docker volume mounts.
    """
    target_dir = os.path.dirname(path)
    fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix=".conf_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except Exception:
        os.unlink(temp_path)
        raise


def create_backup(content: str) -> str:
    """Create a timestamped backup of configuration.yaml. Returns backup path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{CONFIG_FILE}.v2g_backup_{timestamp}"
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(content)
    log.info(f"Backup created: {backup_path}")
    return backup_path


def check_already_present(content: str) -> bool:
    """Check if v2g_pack is already configured."""
    return V2G_PACK_IDENTIFIER in content


def check_packages_dir_include(content: str) -> bool:
    """Check if packages: uses !include_dir_named or similar."""
    return bool(
        re.search(
            r"^\s+packages:\s*!include_dir",
            content,
            re.MULTILINE,
        )
    )


def check_homeassistant_includes_file(content: str) -> bool:
    """Check if homeassistant: section is entirely delegated to another file."""
    return bool(
        re.search(
            r"^homeassistant:\s*!include\s",
            content,
            re.MULTILINE,
        )
    )


def find_homeassistant_line(lines: list) -> int | None:
    """Find the line index of the first top-level 'homeassistant:' key."""
    for i, line in enumerate(lines):
        if re.match(r"^homeassistant:\s*$", line):
            return i
    return None


def find_packages_line(lines: list, ha_line_idx: int) -> int | None:
    """Find the 'packages:' line within the homeassistant: section."""
    for i in range(ha_line_idx + 1, len(lines)):
        line = lines[i]
        # packages: line indented under homeassistant:
        if re.match(r"^  packages:\s*$", line):
            return i
        # Left the homeassistant: section (new top-level key)
        if re.match(r"^\S", line) and line.strip() != "":
            return None
    return None


def find_last_package_child(lines: list, packages_line_idx: int) -> int:
    """Find the last child line under the packages: section."""
    last_child = packages_line_idx
    for i in range(packages_line_idx + 1, len(lines)):
        line = lines[i]
        # A child of packages: has 4+ spaces of indentation
        if re.match(r"^    \S", line):
            last_child = i
        # A comment at 4+ spaces could also be a child
        elif re.match(r"^    #", line):
            last_child = i
        # Empty line might separate entries but could still be within packages
        elif line.strip() == "":
            continue
        else:
            # Left the packages block
            break
    return last_child


def insert_v2g_pack(content: str) -> tuple:
    """Insert the v2g_pack line into the configuration.

    Returns:
        tuple of (modified_content, description_of_change)
    """
    lines = content.splitlines()

    ha_line_idx = find_homeassistant_line(lines)

    if ha_line_idx is None:
        # No homeassistant: section -- prepend the entire block
        block = "homeassistant:\n  packages:\n" + V2G_PACK_LINE + "\n"
        # Ensure there is a blank line separating from existing content
        if content.strip():
            block += "\n"
        return (
            block + content,
            "Added homeassistant: section with v2g_pack package include",
        )

    packages_line_idx = find_packages_line(lines, ha_line_idx)

    if packages_line_idx is None:
        # homeassistant: exists but no packages: key
        # Insert packages: and v2g_pack right after homeassistant:
        lines.insert(ha_line_idx + 1, "  packages:")
        lines.insert(ha_line_idx + 2, V2G_PACK_LINE)
        return (
            "\n".join(lines) + "\n",
            "Added packages: section with v2g_pack under existing homeassistant:",
        )

    # packages: exists -- find where to insert
    last_child_idx = find_last_package_child(lines, packages_line_idx)
    lines.insert(last_child_idx + 1, V2G_PACK_LINE)
    return (
        "\n".join(lines) + "\n",
        "Added v2g_pack to existing packages: section",
    )


def main() -> int:
    """Main entry point."""
    log.info("Checking configuration.yaml for V2G Liberty package include...")

    # Case: No file exists
    if not os.path.exists(CONFIG_FILE):
        log.info(
            "No configuration.yaml found. Creating with V2G Liberty package include."
        )
        new_content = "homeassistant:\n  packages:\n" + V2G_PACK_LINE + "\n"
        write_file_atomic(CONFIG_FILE, new_content)
        send_persistent_notification(
            title="V2G Liberty: Configuration Created",
            message=(
                "V2G Liberty has created `configuration.yaml` with the "
                "required package include.\n\n"
                "A **Home Assistant restart** is required for "
                "the changes to take effect."
            ),
            notification_id="v2g_config_yaml_updated",
        )
        return 0

    # Read existing file
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Strip NUL bytes that may be present from filesystem issues
    if "\x00" in content:
        log.warning(
            "configuration.yaml contains NUL (\\x00) bytes. "
            "Stripping them before processing."
        )
        content = content.replace("\x00", "")

    # Case: Already present
    if check_already_present(content):
        log.info("V2G Liberty package include already present. No changes needed.")
        return 0

    # Case: packages uses !include_dir_named
    if check_packages_dir_include(content):
        log.info(
            "packages: uses !include_dir_named or similar. "
            "V2G Liberty package files are already copied to the packages "
            "directory. No modification needed."
        )
        return 0

    # Case: homeassistant: includes another file
    if check_homeassistant_includes_file(content):
        log.warning(
            "homeassistant: section uses !include to reference another file. "
            "Cannot automatically add V2G Liberty package include."
        )
        send_persistent_notification(
            title="V2G Liberty: Manual Configuration Required",
            message=(
                "V2G Liberty could not automatically add the package include "
                "to your `configuration.yaml` because the `homeassistant:` "
                "section uses `!include` to reference another file.\n\n"
                "Please manually add the following to your homeassistant "
                "packages configuration:\n\n"
                "```yaml\n"
                "v2g_pack: !include "
                "packages/v2g_liberty/v2g_liberty_package.yaml\n"
                "```\n\n"
                "Then restart Home Assistant."
            ),
            notification_id="v2g_config_yaml_manual",
        )
        return 0

    # Cases that require modification: create backup first
    backup_path = create_backup(content)
    modified_content, description = insert_v2g_pack(content)

    write_file_atomic(CONFIG_FILE, modified_content)

    log.info(f"configuration.yaml updated: {description}")

    send_persistent_notification(
        title="V2G Liberty: Configuration Updated",
        message=(
            f"V2G Liberty has automatically updated your "
            f"`configuration.yaml`:\n\n"
            f"**{description}.**\n\n"
            f"A backup has been saved to `{backup_path}`.\n\n"
            f"A **Home Assistant restart** is required for the changes "
            f"to take effect.\n\n"
            f"If you experience any issues, restore the backup and "
            f"add the line manually."
        ),
        notification_id="v2g_config_yaml_updated",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
