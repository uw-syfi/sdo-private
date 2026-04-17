"""Tests for command validation against dangerous patterns."""

import pytest

from app_operator.core import (
    DangerousCommandError,
    validate_command,
)


class TestValidateCommandRejectsDangerous:
    """Dangerous commands must be rejected."""

    def test_rm_rf_root(self):
        with pytest.raises(DangerousCommandError, match="rm -rf /"):
            validate_command("rm -rf /")

    def test_rm_rf_root_with_preceding_command(self):
        with pytest.raises(DangerousCommandError):
            validate_command("echo hello && rm -rf /")

    def test_rm_fr_root(self):
        with pytest.raises(DangerousCommandError, match="rm -fr /"):
            validate_command("rm -fr /")

    def test_mkfs(self):
        with pytest.raises(DangerousCommandError, match="mkfs"):
            validate_command("mkfs.ext4 /dev/sda1")

    def test_mkfs_in_pipeline(self):
        with pytest.raises(DangerousCommandError, match="mkfs"):
            validate_command("echo y | mkfs /dev/sda")

    def test_dd_if(self):
        with pytest.raises(DangerousCommandError, match="dd"):
            validate_command("dd if=/dev/zero of=/dev/sda bs=1M")

    def test_redirect_to_etc(self):
        with pytest.raises(DangerousCommandError, match="system directory"):
            validate_command("echo 'bad' > /etc/passwd")

    def test_redirect_to_usr(self):
        with pytest.raises(DangerousCommandError, match="system directory"):
            validate_command("echo 'x' > /usr/bin/python3")

    def test_redirect_to_boot(self):
        with pytest.raises(DangerousCommandError, match="system directory"):
            validate_command("echo 'x' > /boot/grub/grub.cfg")

    def test_tee_to_etc(self):
        with pytest.raises(DangerousCommandError, match="tee"):
            validate_command("echo 'bad' | tee /etc/hosts")

    def test_cp_to_etc(self):
        with pytest.raises(DangerousCommandError, match="cp"):
            validate_command("cp malicious.conf /etc/nginx/nginx.conf")

    def test_mv_to_usr(self):
        with pytest.raises(DangerousCommandError, match="mv"):
            validate_command("mv payload /usr/local/bin/exploit")


class TestValidateCommandAllowsSafe:
    """Safe commands must be allowed through."""

    def test_simple_ls(self):
        validate_command("ls -la")

    def test_docker_compose_up(self):
        validate_command("docker compose up -d")

    def test_rm_file_in_project(self):
        validate_command("rm -rf ./build")

    def test_rm_named_directory(self):
        validate_command("rm -rf /tmp/myapp")

    def test_cat_etc_file(self):
        validate_command("cat /etc/hosts")

    def test_grep_in_usr(self):
        validate_command("grep -r pattern /usr/share/doc")

    def test_echo_simple(self):
        validate_command("echo hello world")

    def test_cp_within_project(self):
        validate_command("cp config.yaml config.yaml.bak")

    def test_dd_without_if(self):
        validate_command("dd --help")

    def test_empty_command(self):
        validate_command("")


class TestDangerousCommandError:
    """DangerousCommandError stores context."""

    def test_attributes(self):
        err = DangerousCommandError("rm -rf /", "delete root")
        assert err.command == "rm -rf /"
        assert err.reason == "delete root"

    def test_is_value_error(self):
        err = DangerousCommandError("cmd", "reason")
        assert isinstance(err, ValueError)
