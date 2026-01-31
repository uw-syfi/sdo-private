"""Tests for DSPy module loader."""

import json

from app_operator.dspy_integration.loader import (
    DSPyModuleCache,
    get_cache,
    reset_cache,
    resolve_version,
    load_optimized_module,
)


class TestDSPyModuleCache:
    """Tests for DSPy module cache."""

    def test_cache_get_miss(self):
        """Getting nonexistent key should return None."""
        cache = DSPyModuleCache()
        assert cache.get('nonexistent') is None

    def test_cache_set_and_get(self):
        """Should be able to store and retrieve modules."""
        cache = DSPyModuleCache()
        mock_module = object()

        cache.set('deployer_fix_error:v1', mock_module)
        result = cache.get('deployer_fix_error:v1')

        assert result is mock_module

    def test_cache_clear(self):
        """Clear should remove all entries."""
        cache = DSPyModuleCache()
        cache.set('key1', object())
        cache.set('key2', object())

        assert len(cache) == 2

        cache.clear()

        assert len(cache) == 0
        assert cache.get('key1') is None
        assert cache.get('key2') is None

    def test_cache_len(self):
        """Len should return number of cached items."""
        cache = DSPyModuleCache()
        assert len(cache) == 0

        cache.set('key1', object())
        assert len(cache) == 1

        cache.set('key2', object())
        assert len(cache) == 2


class TestGlobalCache:
    """Tests for global cache functions."""

    def test_get_cache_returns_singleton(self):
        """get_cache should return the same instance."""
        cache1 = get_cache()
        cache2 = get_cache()
        assert cache1 is cache2

    def test_reset_cache_clears(self):
        """reset_cache should clear the global cache."""
        cache = get_cache()
        cache.set('key', object())
        assert len(cache) == 1

        reset_cache()

        assert len(cache) == 0


class TestResolveVersion:
    """Tests for version resolution."""

    def test_resolve_direct_version(self, tmp_path):
        """Should resolve existing version directory."""
        optimized_dir = tmp_path / "optimized"
        optimized_dir.mkdir()
        (optimized_dir / "v1").mkdir()
        (optimized_dir / "v2").mkdir()

        result = resolve_version(optimized_dir, "v1")
        assert result == "v1"

        result = resolve_version(optimized_dir, "v2")
        assert result == "v2"

    def test_resolve_latest(self, tmp_path):
        """latest should resolve to highest version number."""
        optimized_dir = tmp_path / "optimized"
        optimized_dir.mkdir()
        (optimized_dir / "v1").mkdir()
        (optimized_dir / "v2").mkdir()
        (optimized_dir / "v3").mkdir()

        result = resolve_version(optimized_dir, "latest")
        assert result == "v3"

    def test_resolve_latest_single_version(self, tmp_path):
        """latest with single version should return that version."""
        optimized_dir = tmp_path / "optimized"
        optimized_dir.mkdir()
        (optimized_dir / "v1").mkdir()

        result = resolve_version(optimized_dir, "latest")
        assert result == "v1"

    def test_resolve_latest_no_versions(self, tmp_path):
        """latest with no versions should return None."""
        optimized_dir = tmp_path / "optimized"
        optimized_dir.mkdir()

        result = resolve_version(optimized_dir, "latest")
        assert result is None

    def test_resolve_nonexistent_version(self, tmp_path):
        """Nonexistent version should return None."""
        optimized_dir = tmp_path / "optimized"
        optimized_dir.mkdir()
        (optimized_dir / "v1").mkdir()

        result = resolve_version(optimized_dir, "v99")
        assert result is None

    def test_resolve_nonexistent_dir(self, tmp_path):
        """Nonexistent optimized_dir should return None."""
        result = resolve_version(tmp_path / "nonexistent", "v1")
        assert result is None

    def test_resolve_latest_skips_non_version_dirs(self, tmp_path):
        """latest should ignore directories not matching vN pattern."""
        optimized_dir = tmp_path / "optimized"
        optimized_dir.mkdir()
        (optimized_dir / "v1").mkdir()
        (optimized_dir / "v2").mkdir()
        (optimized_dir / "backup").mkdir()  # Should be ignored
        (optimized_dir / "vX").mkdir()      # Should be ignored (not numeric)

        result = resolve_version(optimized_dir, "latest")
        assert result == "v2"


class TestLoadOptimizedModule:
    """Tests for loading optimized DSPy modules."""

    def setup_method(self):
        """Reset cache before each test."""
        reset_cache()

    def test_load_module_not_found(self, tmp_path):
        """Should return None if module file doesn't exist."""
        optimized_dir = tmp_path / "optimized"
        optimized_dir.mkdir()
        (optimized_dir / "v1").mkdir()

        result = load_optimized_module('deployer_fix_error', optimized_dir, 'v1')
        assert result is None

    def test_load_module_no_version_dir(self, tmp_path):
        """Should return None if version directory doesn't exist."""
        optimized_dir = tmp_path / "optimized"
        optimized_dir.mkdir()

        result = load_optimized_module('deployer_fix_error', optimized_dir, 'v99')
        assert result is None

    def test_load_module_corrupted_json(self, tmp_path):
        """Should return None if JSON is corrupted."""
        optimized_dir = tmp_path / "optimized"
        version_dir = optimized_dir / "v1"
        version_dir.mkdir(parents=True)

        module_file = version_dir / "deployer_fix_error.dspy.json"
        module_file.write_text("invalid json{")

        result = load_optimized_module('deployer_fix_error', optimized_dir, 'v1')
        assert result is None

    def test_load_module_success(self, tmp_path):
        """Should successfully load valid module."""
        optimized_dir = tmp_path / "optimized"
        version_dir = optimized_dir / "v1"
        version_dir.mkdir(parents=True)

        # Create a valid module file
        module_file = version_dir / "deployer_fix_error.dspy.json"
        module_data = {
            "demos": [
                {
                    "repo_path": "/repo1",
                    "error_context": "Error 1",
                    "fix_summary": "Fixed by doing X"
                }
            ]
        }
        module_file.write_text(json.dumps(module_data))

        result = load_optimized_module('deployer_fix_error', optimized_dir, 'v1')

        assert result is not None
        assert hasattr(result, 'demos')
        assert len(result.demos) == 1

    def test_load_module_caching(self, tmp_path):
        """Second load should hit cache."""
        optimized_dir = tmp_path / "optimized"
        version_dir = optimized_dir / "v1"
        version_dir.mkdir(parents=True)

        module_file = version_dir / "deployer_fix_error.dspy.json"
        module_file.write_text(json.dumps({"demos": []}))

        # First load
        result1 = load_optimized_module('deployer_fix_error', optimized_dir, 'v1')

        # Second load should return same instance
        result2 = load_optimized_module('deployer_fix_error', optimized_dir, 'v1')

        assert result1 is result2

    def test_load_module_latest_version(self, tmp_path):
        """Should resolve 'latest' to highest version."""
        optimized_dir = tmp_path / "optimized"
        (optimized_dir / "v1").mkdir(parents=True)
        (optimized_dir / "v2").mkdir(parents=True)

        # Create module in v2
        module_file = optimized_dir / "v2" / "deployer_fix_error.dspy.json"
        module_file.write_text(json.dumps({"demos": [{"marker": "v2"}]}))

        result = load_optimized_module('deployer_fix_error', optimized_dir, 'latest')

        assert result is not None
        assert len(result.demos) == 1
        assert result.demos[0]["marker"] == "v2"

    def test_load_module_invalid_signature(self, tmp_path):
        """Should return None if prompt name has no signature."""
        optimized_dir = tmp_path / "optimized"
        version_dir = optimized_dir / "v1"
        version_dir.mkdir(parents=True)

        module_file = version_dir / "invalid_prompt.dspy.json"
        module_file.write_text(json.dumps({"demos": []}))

        result = load_optimized_module('invalid_prompt', optimized_dir, 'v1')
        assert result is None
