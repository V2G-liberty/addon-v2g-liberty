"""Unit tests for ensure_ha_package_include.py.

Tests the automatic configuration.yaml modification logic that adds the
V2G Liberty package include during add-on startup.
"""

import os
from unittest.mock import patch

import pytest

from ensure_ha_package_include import (
    V2G_PACK_IDENTIFIER,
    V2G_PACK_LINE,
    check_already_present,
    check_homeassistant_includes_file,
    check_packages_dir_include,
    create_backup,
    find_homeassistant_line,
    find_last_package_child,
    find_packages_line,
    insert_v2g_pack,
    main,
)

# --- Fixtures ---


@pytest.fixture
def config_dir(tmp_path):
    """Provide a temporary directory for configuration.yaml."""
    return tmp_path


@pytest.fixture
def config_file(config_dir):
    """Provide the path to a temporary configuration.yaml."""
    return str(config_dir / "configuration.yaml")


# --- Test check_already_present ---


class TestCheckAlreadyPresent:
    def test_present(self):
        content = "homeassistant:\n  packages:\n    v2g_pack: !include foo.yaml\n"
        assert check_already_present(content) is True

    def test_not_present(self):
        content = "homeassistant:\n  packages:\n    other_pack: !include bar.yaml\n"
        assert check_already_present(content) is False

    def test_empty_file(self):
        assert check_already_present("") is False

    def test_present_with_comment(self):
        content = "# v2g_pack: this is a comment\n"
        assert check_already_present(content) is True


# --- Test check_packages_dir_include ---


class TestCheckPackagesDirInclude:
    def test_include_dir_named(self):
        content = "homeassistant:\n  packages: !include_dir_named packages/\n"
        assert check_packages_dir_include(content) is True

    def test_include_dir_merge_named(self):
        content = "homeassistant:\n  packages: !include_dir_merge_named packages/\n"
        assert check_packages_dir_include(content) is True

    def test_normal_packages(self):
        content = "homeassistant:\n  packages:\n    my_pack: !include foo.yaml\n"
        assert check_packages_dir_include(content) is False


# --- Test check_homeassistant_includes_file ---


class TestCheckHomeassistantIncludesFile:
    def test_includes_file(self):
        content = "homeassistant: !include ha_config.yaml\n"
        assert check_homeassistant_includes_file(content) is True

    def test_normal_homeassistant(self):
        content = "homeassistant:\n  packages:\n"
        assert check_homeassistant_includes_file(content) is False

    def test_no_homeassistant(self):
        content = "default_config:\n"
        assert check_homeassistant_includes_file(content) is False


# --- Test find_homeassistant_line ---


class TestFindHomeassistantLine:
    def test_found(self):
        lines = ["default_config:", "homeassistant:", "  packages:"]
        assert find_homeassistant_line(lines) == 1

    def test_not_found(self):
        lines = ["default_config:", "automation: !include automations.yaml"]
        assert find_homeassistant_line(lines) is None

    def test_indented_ignored(self):
        """An indented homeassistant: key should not match."""
        lines = ["other:", "  homeassistant:", "    packages:"]
        assert find_homeassistant_line(lines) is None


# --- Test find_packages_line ---


class TestFindPackagesLine:
    def test_found(self):
        lines = ["homeassistant:", "  packages:", "    my_pack: !include foo.yaml"]
        assert find_packages_line(lines, 0) == 1

    def test_not_found_different_section(self):
        lines = ["homeassistant:", "  name: My Home", "default_config:"]
        assert find_packages_line(lines, 0) is None

    def test_not_found_no_packages(self):
        lines = ["homeassistant:", "  name: My Home"]
        assert find_packages_line(lines, 0) is None


# --- Test find_last_package_child ---


class TestFindLastPackageChild:
    def test_single_child(self):
        lines = [
            "homeassistant:",
            "  packages:",
            "    my_pack: !include foo.yaml",
            "default_config:",
        ]
        assert find_last_package_child(lines, 1) == 2

    def test_multiple_children(self):
        lines = [
            "homeassistant:",
            "  packages:",
            "    pack_a: !include a.yaml",
            "    pack_b: !include b.yaml",
            "default_config:",
        ]
        assert find_last_package_child(lines, 1) == 3

    def test_children_with_blank_line_between(self):
        lines = [
            "homeassistant:",
            "  packages:",
            "    pack_a: !include a.yaml",
            "",
            "    pack_b: !include b.yaml",
            "default_config:",
        ]
        assert find_last_package_child(lines, 1) == 4

    def test_children_with_comments(self):
        lines = [
            "homeassistant:",
            "  packages:",
            "    # My custom package",
            "    my_pack: !include foo.yaml",
            "default_config:",
        ]
        assert find_last_package_child(lines, 1) == 3


# --- Test insert_v2g_pack ---


class TestInsertV2gPack:
    def test_no_homeassistant_section(self):
        """Prepend full block when no homeassistant: section exists."""
        content = "default_config:\n\nautomation: !include automations.yaml\n"
        result, description = insert_v2g_pack(content)
        assert V2G_PACK_IDENTIFIER in result
        assert result.startswith("homeassistant:\n  packages:\n")
        assert "default_config:" in result
        assert "Added homeassistant: section" in description

    def test_homeassistant_no_packages(self):
        """Add packages: section when homeassistant: exists without it."""
        content = "homeassistant:\n  name: My Home\ndefault_config:\n"
        result, description = insert_v2g_pack(content)
        assert V2G_PACK_IDENTIFIER in result
        assert "  packages:" in result
        assert "name: My Home" in result
        assert "Added packages: section" in description

    def test_packages_exists_no_v2g(self):
        """Add v2g_pack to existing packages: section."""
        content = (
            "homeassistant:\n"
            "  packages:\n"
            "    other_pack: !include other.yaml\n"
            "default_config:\n"
        )
        result, description = insert_v2g_pack(content)
        assert V2G_PACK_IDENTIFIER in result
        assert "other_pack: !include other.yaml" in result
        assert "Added v2g_pack to existing packages" in description

    def test_empty_file(self):
        """Handle completely empty content."""
        result, description = insert_v2g_pack("")
        assert V2G_PACK_IDENTIFIER in result
        assert result.startswith("homeassistant:\n  packages:\n")

    def test_preserves_comments(self):
        """Comments in the file are preserved."""
        content = (
            "# My HA config\n"
            "homeassistant:\n"
            "  packages:\n"
            "    # Existing package\n"
            "    my_pack: !include my.yaml\n"
            "# End of file\n"
        )
        result, _ = insert_v2g_pack(content)
        assert "# My HA config" in result
        assert "# Existing package" in result
        assert "# End of file" in result
        assert V2G_PACK_IDENTIFIER in result

    def test_preserves_other_top_level_keys(self):
        """Other top-level keys like default_config, automation, etc. preserved."""
        content = (
            "homeassistant:\n"
            "  name: My Home\n"
            "\n"
            "default_config:\n"
            "\n"
            "automation: !include automations.yaml\n"
            "script: !include scripts.yaml\n"
        )
        result, _ = insert_v2g_pack(content)
        assert "default_config:" in result
        assert "automation: !include automations.yaml" in result
        assert "script: !include scripts.yaml" in result

    def test_multiple_existing_packages(self):
        """v2g_pack is added after the last existing package."""
        content = (
            "homeassistant:\n"
            "  packages:\n"
            "    pack_a: !include a.yaml\n"
            "    pack_b: !include b.yaml\n"
            "    pack_c: !include c.yaml\n"
            "default_config:\n"
        )
        result, _ = insert_v2g_pack(content)
        lines = result.splitlines()
        v2g_idx = next(i for i, l in enumerate(lines) if V2G_PACK_IDENTIFIER in l)
        pack_c_idx = next(i for i, l in enumerate(lines) if "pack_c:" in l)
        assert v2g_idx == pack_c_idx + 1

    def test_trailing_newline_preserved_no_packages(self):
        """Output ends with newline when inserting packages section."""
        content = "homeassistant:\n  name: My Home\ndefault_config:\n"
        result, _ = insert_v2g_pack(content)
        assert result.endswith("\n")

    def test_trailing_newline_preserved_with_packages(self):
        """Output ends with newline when adding to existing packages."""
        content = (
            "homeassistant:\n"
            "  packages:\n"
            "    other_pack: !include other.yaml\n"
            "default_config:\n"
        )
        result, _ = insert_v2g_pack(content)
        assert result.endswith("\n")

    def test_empty_packages_section(self):
        """v2g_pack is added to an empty packages: section."""
        content = "homeassistant:\n  packages:\n\ndefault_config:\n"
        result, _ = insert_v2g_pack(content)
        assert V2G_PACK_IDENTIFIER in result
        assert "default_config:" in result
        assert result.endswith("\n")


# --- Test create_backup ---


class TestCreateBackup:
    def test_backup_created(self, config_dir):
        """Backup file is created with original content."""
        original = "homeassistant:\n  name: My Home\n"
        config_path = str(config_dir / "configuration.yaml")

        with patch(
            "ensure_ha_package_include.CONFIG_FILE",
            config_path,
        ):
            backup_path = create_backup(original)

        assert os.path.exists(backup_path)
        with open(backup_path) as f:
            assert f.read() == original

    def test_backup_has_timestamp(self, config_dir):
        """Backup filename contains a timestamp."""
        config_path = str(config_dir / "configuration.yaml")

        with patch(
            "ensure_ha_package_include.CONFIG_FILE",
            config_path,
        ):
            backup_path = create_backup("test content")

        assert ".v2g_backup_" in backup_path


# --- Test main() integration ---


class TestMain:
    def test_no_file_creates_new(self, config_file):
        """When no configuration.yaml exists, create one."""
        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch("ensure_ha_package_include.send_persistent_notification"),
        ):
            result = main()

        assert result == 0
        with open(config_file) as f:
            content = f.read()
        assert V2G_PACK_IDENTIFIER in content
        assert content.startswith("homeassistant:\n")

    def test_already_present_no_change(self, config_file):
        """When v2g_pack already present, file is not modified."""
        original = (
            "homeassistant:\n"
            "  packages:\n"
            "    v2g_pack: !include packages/v2g_liberty/v2g_liberty_package.yaml\n"
        )
        with open(config_file, "w") as f:
            f.write(original)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch(
                "ensure_ha_package_include.send_persistent_notification"
            ) as mock_notify,
        ):
            result = main()

        assert result == 0
        with open(config_file) as f:
            assert f.read() == original
        mock_notify.assert_not_called()

    def test_include_dir_no_change(self, config_file):
        """When packages uses !include_dir, file is not modified."""
        original = "homeassistant:\n  packages: !include_dir_named packages/\n"
        with open(config_file, "w") as f:
            f.write(original)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch(
                "ensure_ha_package_include.send_persistent_notification"
            ) as mock_notify,
        ):
            result = main()

        assert result == 0
        with open(config_file) as f:
            assert f.read() == original
        mock_notify.assert_not_called()

    def test_homeassistant_include_sends_notification(self, config_file):
        """When homeassistant: uses !include, notify user for manual action."""
        original = "homeassistant: !include ha_config.yaml\n"
        with open(config_file, "w") as f:
            f.write(original)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch(
                "ensure_ha_package_include.send_persistent_notification"
            ) as mock_notify,
        ):
            result = main()

        assert result == 0
        # File not modified
        with open(config_file) as f:
            assert f.read() == original
        # Notification sent
        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args
        assert "Manual" in call_kwargs[1]["title"] or "Manual" in call_kwargs[0][0]

    def test_modifies_and_creates_backup(self, config_file, config_dir):
        """When modification is needed, backup is created and file is updated."""
        original = "homeassistant:\n  name: My Home\ndefault_config:\n"
        with open(config_file, "w") as f:
            f.write(original)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch("ensure_ha_package_include.send_persistent_notification"),
        ):
            result = main()

        assert result == 0
        # File was modified
        with open(config_file) as f:
            content = f.read()
        assert V2G_PACK_IDENTIFIER in content
        assert "name: My Home" in content

        # Backup was created
        backups = [f for f in os.listdir(config_dir) if ".v2g_backup_" in f]
        assert len(backups) == 1
        with open(config_dir / backups[0]) as f:
            assert f.read() == original

    def test_adds_to_existing_packages(self, config_file):
        """Add v2g_pack alongside existing packages."""
        original = (
            "homeassistant:\n"
            "  packages:\n"
            "    other_pack: !include other.yaml\n"
            "\n"
            "default_config:\n"
        )
        with open(config_file, "w") as f:
            f.write(original)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch("ensure_ha_package_include.send_persistent_notification"),
        ):
            result = main()

        assert result == 0
        with open(config_file) as f:
            content = f.read()
        assert V2G_PACK_IDENTIFIER in content
        assert "other_pack: !include other.yaml" in content
        assert "default_config:" in content

    def test_no_homeassistant_section(self, config_file):
        """When file exists but has no homeassistant: section, prepend it."""
        original = "default_config:\n\nautomation: !include automations.yaml\n"
        with open(config_file, "w") as f:
            f.write(original)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch("ensure_ha_package_include.send_persistent_notification"),
        ):
            result = main()

        assert result == 0
        with open(config_file) as f:
            content = f.read()
        assert content.startswith("homeassistant:\n")
        assert V2G_PACK_IDENTIFIER in content
        assert "default_config:" in content
        assert "automation: !include automations.yaml" in content

    def test_typical_ha_configuration(self, config_file):
        """Test with a typical Home Assistant configuration.yaml layout."""
        original = (
            "# Configure a default setup of Home Assistant\n"
            "default_config:\n"
            "\n"
            "homeassistant:\n"
            "  name: My Smart Home\n"
            "  unit_system: metric\n"
            "  currency: EUR\n"
            "\n"
            "automation: !include automations.yaml\n"
            "script: !include scripts.yaml\n"
            "scene: !include scenes.yaml\n"
        )
        with open(config_file, "w") as f:
            f.write(original)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch("ensure_ha_package_include.send_persistent_notification"),
        ):
            result = main()

        assert result == 0
        with open(config_file) as f:
            content = f.read()

        assert V2G_PACK_IDENTIFIER in content
        # All original content preserved
        assert "# Configure a default setup" in content
        assert "default_config:" in content
        assert "name: My Smart Home" in content
        assert "unit_system: metric" in content
        assert "automation: !include automations.yaml" in content

    def test_notification_sent_on_modification(self, config_file):
        """Persistent notification is sent when file is modified."""
        original = "default_config:\n"
        with open(config_file, "w") as f:
            f.write(original)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch(
                "ensure_ha_package_include.send_persistent_notification"
            ) as mock_notify,
        ):
            main()

        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert "Updated" in call_args[1]["title"] or "Updated" in call_args[0][0]

    def test_nul_bytes_stripped(self, config_file):
        """NUL bytes in the file are stripped before processing."""
        # File with NUL bytes appended (common filesystem artefact)
        original_with_nul = (
            "homeassistant:\n  name: My Home\ndefault_config:\n\x00\x00\x00\x00"
        )
        with open(config_file, "w") as f:
            f.write(original_with_nul)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch("ensure_ha_package_include.send_persistent_notification"),
        ):
            result = main()

        assert result == 0
        with open(config_file) as f:
            content = f.read()
        assert V2G_PACK_IDENTIFIER in content
        assert "\x00" not in content

    def test_nul_bytes_already_present_still_detected(self, config_file):
        """v2g_pack detection works even with NUL bytes in the file."""
        original_with_nul = (
            "homeassistant:\n"
            "  packages:\n"
            "    v2g_pack: !include packages/v2g_liberty/v2g_liberty_package.yaml\n"
            "\x00\x00\x00"
        )
        with open(config_file, "w") as f:
            f.write(original_with_nul)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch(
                "ensure_ha_package_include.send_persistent_notification"
            ) as mock_notify,
        ):
            result = main()

        assert result == 0
        # Should detect v2g_pack even with NUL bytes and not modify
        mock_notify.assert_not_called()

    def test_output_ends_with_newline(self, config_file):
        """Modified file always ends with a newline."""
        original = "homeassistant:\n  name: My Home\ndefault_config:\n"
        with open(config_file, "w") as f:
            f.write(original)

        with (
            patch("ensure_ha_package_include.CONFIG_FILE", config_file),
            patch("ensure_ha_package_include.send_persistent_notification"),
        ):
            main()

        with open(config_file) as f:
            content = f.read()
        assert content.endswith("\n")
