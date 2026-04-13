"""DSPy module loading and caching.

Handles loading optimized DSPy modules from disk with caching and version resolution.
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import dspy

from app_operator.dspy_integration.signatures import get_signature

logger = logging.getLogger(__name__)

_CANDIDATE_VERSION_RE = re.compile(r"^eval_(\d+)_c(\d+)$")


class DSPyModuleCache:
    """Cache for loaded DSPy modules to avoid repeated disk I/O."""

    def __init__(self):
        """Initialize empty cache."""
        self._cache: dict[str, dspy.Module] = {}

    def get(self, key: str) -> dspy.Module | None:
        """Get a cached module.

        Args:
            key: Cache key (prompt_name:version)

        Returns:
            Cached module or None if not found
        """
        return self._cache.get(key)

    def set(self, key: str, module: dspy.Module) -> None:
        """Store a module in the cache.

        Args:
            key: Cache key (prompt_name:version)
            module: DSPy module to cache
        """
        self._cache[key] = module

    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()

    def __len__(self) -> int:
        """Get cache size."""
        return len(self._cache)


# Global cache instance
_module_cache = DSPyModuleCache()


def get_cache() -> DSPyModuleCache:
    """Get the global module cache."""
    return _module_cache


def reset_cache() -> None:
    """Reset the global cache (for testing)."""
    _module_cache.clear()


def resolve_version(optimized_dir: Path, version: str) -> str | None:
    """Resolve version string to actual version directory.

    Handles special cases like 'latest' which points to the most recent version.

    Args:
        optimized_dir: Base directory containing versioned subdirectories
        version: Version string (e.g., 'v1', 'latest')

    Returns:
        Resolved version string (e.g., 'v3') or None if not found

    Examples:
        >>> resolve_version(Path('prompts/optimized'), 'latest')
        'v3'  # If v3 is the latest
        >>> resolve_version(Path('prompts/optimized'), 'v2')
        'v2'  # Direct match
    """
    if not optimized_dir.exists():
        logger.warning(f"Optimized prompts directory not found: {optimized_dir}")
        return None

    # Handle 'latest' - find highest version number
    if version == "latest":
        version_dirs = [
            d for d in optimized_dir.iterdir() if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()
        ]
        if not version_dirs:
            logger.warning(f"No versioned directories found in {optimized_dir}")
            return None

        # Sort by version number (v1, v2, v3, ...)
        version_dirs.sort(key=lambda d: int(d.name[1:]))
        resolved = version_dirs[-1].name
        logger.debug(f"Resolved 'latest' to {resolved}")
        return resolved

    # Direct version match
    version_dir = optimized_dir / version
    if version_dir.exists():
        return version
    logger.warning(f"Version directory not found: {version_dir}")
    return None


def load_optimized_module(prompt_name: str, optimized_dir: Path, version: str = "latest") -> dspy.Module | None:
    """Load an optimized DSPy module from disk.

    Args:
        prompt_name: Name of the prompt (e.g., 'deployer_fix_error')
        optimized_dir: Base directory containing versioned optimized prompts
        version: Version to load (e.g., 'v1', 'latest')

    Returns:
        Loaded DSPy module, or None on failure (missing files,
        corrupted data, invalid signatures).
    """
    module, _metadata = load_optimized_module_with_metadata(prompt_name, optimized_dir, version)
    return module


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_candidate_ref(candidate_ref: str, family_prefix: str) -> str:
    """Return family-qualified candidate reference."""
    ref = candidate_ref.strip()
    if not ref:
        return ref
    if "/" in ref:
        return ref
    if family_prefix:
        return f"{family_prefix}/{ref}"
    return ref


def _extract_lineage_from_metadata(
    metadata_path: Path,
    resolved_version: str,
) -> tuple[str | None, list[str], str]:
    """Extract candidate and parent identifiers from version metadata."""
    if not metadata_path.exists():
        return None, [], "none"

    try:
        metadata = json.loads(metadata_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None, [], "none"

    family_prefix = resolved_version.rsplit("/", 1)[0] if "/" in resolved_version else ""

    lineage = metadata.get("lineage", {})
    candidate_id_raw = ""
    if isinstance(lineage, dict):
        candidate_id_raw = str(lineage.get("selected_candidate_id") or "").strip()
    if not candidate_id_raw:
        iter_num = metadata.get("iteration")
        best_idx = metadata.get("best_candidate_index")
        if isinstance(iter_num, int) and isinstance(best_idx, int):
            candidate_id_raw = f"eval_{iter_num}_c{best_idx}"

    candidate_id = _canonical_candidate_ref(candidate_id_raw, family_prefix) if candidate_id_raw else None

    parent_candidates_raw: list[str] = []
    if isinstance(lineage, dict):
        raw_parents = lineage.get("parent_candidate_ids", [])
        if isinstance(raw_parents, list):
            parent_candidates_raw.extend(str(p) for p in raw_parents if str(p).strip())
    if not parent_candidates_raw:
        recombination = metadata.get("recombination", {})
        if isinstance(recombination, dict):
            raw_parents = recombination.get("parent_candidates", [])
            if isinstance(raw_parents, list):
                parent_candidates_raw.extend(str(p) for p in raw_parents if str(p).strip())

    parent_candidates = [_canonical_candidate_ref(parent, family_prefix) for parent in parent_candidates_raw]
    parent_candidates = list(dict.fromkeys(parent_candidates))

    metadata_hash = _metadata_sha256(metadata)
    return candidate_id, parent_candidates, metadata_hash


def _infer_candidate_id_from_version(resolved_version: str) -> str | None:
    """Infer candidate identifier directly from version string."""
    version_leaf = resolved_version.rsplit("/", 1)[-1]
    if _CANDIDATE_VERSION_RE.match(version_leaf):
        return resolved_version
    return None


def load_optimized_module_with_metadata(
    prompt_name: str,
    optimized_dir: Path,
    version: str = "latest",
) -> tuple[dspy.Module | None, dict[str, Any] | None]:
    """Load optimized module and return runtime artifact metadata."""
    # Check cache first (requested key)
    requested_key = f"{prompt_name}:{version}"
    cached = _module_cache.get(requested_key)
    if cached is not None:
        logger.debug(f"Cache hit for {requested_key}")
        resolved_for_cache = resolve_version(optimized_dir, version) or version
        version_dir = optimized_dir / resolved_for_cache
        module_file = version_dir / f"{prompt_name}.dspy.json"
        metadata_file = version_dir / "metadata.json"
        candidate_id, parent_ids, metadata_hash = _extract_lineage_from_metadata(metadata_file, resolved_for_cache)
        if candidate_id is None:
            candidate_id = _infer_candidate_id_from_version(resolved_for_cache)
        artifact = {
            "renderer": "dspy",
            "prompt_name": prompt_name,
            "configured_version": version,
            "resolved_version": resolved_for_cache,
            "module_file": str(module_file),
            "module_sha256": _file_sha256(module_file) if module_file.exists() else "",
            "metadata_file": str(metadata_file) if metadata_file.exists() else "",
            "metadata_sha256": metadata_hash,
            "candidate_id": candidate_id,
            "parent_candidate_ids": parent_ids,
            "experiment_family": resolved_for_cache.rsplit("/", 1)[0] if "/" in resolved_for_cache else "",
        }
        return cached, artifact

    # Resolve version
    resolved_version = resolve_version(optimized_dir, version)
    if resolved_version is None:
        logger.error(f"Could not resolve version '{version}' in {optimized_dir}")
        return None, None

    # Check cache after resolution
    resolved_key = f"{prompt_name}:{resolved_version}"
    cached = _module_cache.get(resolved_key)
    if cached is not None:
        logger.debug(f"Cache hit for {resolved_key} (after version resolution)")
        metadata_file = optimized_dir / resolved_version / "metadata.json"
        candidate_id, parent_ids, metadata_hash = _extract_lineage_from_metadata(metadata_file, resolved_version)
        if candidate_id is None:
            candidate_id = _infer_candidate_id_from_version(resolved_version)
        artifact = {
            "renderer": "dspy",
            "prompt_name": prompt_name,
            "configured_version": version,
            "resolved_version": resolved_version,
            "module_file": str(optimized_dir / resolved_version / f"{prompt_name}.dspy.json"),
            "module_sha256": _file_sha256(optimized_dir / resolved_version / f"{prompt_name}.dspy.json"),
            "metadata_file": str(metadata_file) if metadata_file.exists() else "",
            "metadata_sha256": metadata_hash,
            "candidate_id": candidate_id,
            "parent_candidate_ids": parent_ids,
            "experiment_family": resolved_version.rsplit("/", 1)[0] if "/" in resolved_version else "",
        }
        return cached, artifact

    version_dir = optimized_dir / resolved_version
    module_file = version_dir / f"{prompt_name}.dspy.json"
    metadata_file = version_dir / "metadata.json"

    if not module_file.exists():
        logger.error(f"DSPy module file not found: {module_file}")
        return None, None

    try:
        logger.info(f"Loading DSPy module: {module_file}")
        signature_class = get_signature(prompt_name)
        module = dspy.Predict(signature_class)
        with open(module_file) as f:
            state = json.load(f)

        if "demos" in state:
            module.demos = state["demos"]
            logger.debug(f"Loaded {len(module.demos)} demonstrations")

        if state.get("optimized_instruction"):
            module.signature = module.signature.with_instructions(state["optimized_instruction"])
            logger.debug(f"Restored optimized instruction ({len(state['optimized_instruction'])} chars)")

        _module_cache.set(resolved_key, module)
        if version != resolved_version:
            _module_cache.set(requested_key, module)

        candidate_id, parent_ids, metadata_hash = _extract_lineage_from_metadata(metadata_file, resolved_version)
        if candidate_id is None:
            candidate_id = _infer_candidate_id_from_version(resolved_version)

        artifact = {
            "renderer": "dspy",
            "prompt_name": prompt_name,
            "configured_version": version,
            "resolved_version": resolved_version,
            "module_file": str(module_file),
            "module_sha256": _file_sha256(module_file),
            "metadata_file": str(metadata_file) if metadata_file.exists() else "",
            "metadata_sha256": metadata_hash,
            "candidate_id": candidate_id,
            "parent_candidate_ids": parent_ids,
            "experiment_family": resolved_version.rsplit("/", 1)[0] if "/" in resolved_version else "",
        }
        return module, artifact

    except json.JSONDecodeError as e:
        logger.error(f"Corrupted DSPy module file {module_file}: {e}")
        return None, None
    except KeyError as e:
        logger.error(f"Invalid DSPy signature for '{prompt_name}': {e}")
        return None, None
    except (OSError, RuntimeError, AttributeError) as e:
        logger.error(f"Failed to load DSPy module from {module_file}: {e}")
        return None, None
