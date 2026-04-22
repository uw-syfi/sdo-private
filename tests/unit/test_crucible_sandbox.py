"""Tests for the shared crucible SandboxConfig builder."""

from __future__ import annotations

import os

import pytest
from agentshim import SandboxConfig

from sregym_agents.crucible.sandbox import build_crucible_sandbox


class TestBuildCrucibleSandbox:
    def test_returns_sandbox_config(self, tmp_path):
        cfg = build_crucible_sandbox(str(tmp_path))
        assert isinstance(cfg, SandboxConfig)

    def test_exp_cwd_in_allow_read_and_confined_native(self, tmp_path):
        cfg = build_crucible_sandbox(str(tmp_path))
        assert str(tmp_path) in cfg.allow_read
        assert cfg.confine_native_reads_to == [str(tmp_path)]

    def test_system_dirs_in_allow_read(self, tmp_path):
        cfg = build_crucible_sandbox(str(tmp_path))
        for required in ("/bin", "/usr", "/lib", "/etc", "/proc"):
            assert required in cfg.allow_read, f"missing {required} in allow_read"

    def test_kubectl_curl_wget_excluded(self, tmp_path):
        cfg = build_crucible_sandbox(str(tmp_path))
        assert "kubectl *" in cfg.excluded_commands
        assert "curl *" in cfg.excluded_commands
        assert "wget *" in cfg.excluded_commands

    def test_deny_root_read(self, tmp_path):
        cfg = build_crucible_sandbox(str(tmp_path))
        assert cfg.deny_read == ["/"]

    def test_kube_cache_writable(self, tmp_path):
        cfg = build_crucible_sandbox(str(tmp_path))
        assert os.path.expanduser("~/.kube") in cfg.allow_write

    @pytest.mark.parametrize("env_key", ["KUBECONFIG", "SREGYM_BASE_KUBECONFIG"])
    def test_kubeconfig_path_added_to_allow_read(self, tmp_path, monkeypatch, env_key):
        kc = tmp_path / "kc.yaml"
        kc.write_text("")
        monkeypatch.delenv("KUBECONFIG", raising=False)
        monkeypatch.delenv("SREGYM_BASE_KUBECONFIG", raising=False)
        monkeypatch.setenv(env_key, str(kc))
        cfg = build_crucible_sandbox(str(tmp_path))
        assert str(kc) in cfg.allow_read
        assert str(tmp_path) in cfg.allow_read  # kubeconfig parent dir == tmp_path

    def test_kubeconfig_first_entry_used_when_pathsep(self, tmp_path, monkeypatch):
        kc1 = tmp_path / "a.yaml"
        kc2 = tmp_path / "b.yaml"
        kc1.write_text("")
        kc2.write_text("")
        monkeypatch.delenv("SREGYM_BASE_KUBECONFIG", raising=False)
        monkeypatch.setenv("KUBECONFIG", f"{kc1}{os.pathsep}{kc2}")
        cfg = build_crucible_sandbox(str(tmp_path))
        assert str(kc1) in cfg.allow_read
        assert str(kc2) not in cfg.allow_read
