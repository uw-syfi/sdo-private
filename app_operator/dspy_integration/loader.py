"""DSPy module loading and caching.

Handles loading optimized DSPy modules from disk with caching and version resolution.
"""

import json
import logging
from pathlib import Path

import dspy

from app_operator.dspy_integration.signatures import get_signature

logger = logging.getLogger(__name__)


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
            d for d in optimized_dir.iterdir()
            if d.is_dir() and d.name.startswith('v') and d.name[1:].isdigit()
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
    else:
        logger.warning(f"Version directory not found: {version_dir}")
        return None


def load_optimized_module(
    prompt_name: str,
    optimized_dir: Path,
    version: str = "latest"
) -> dspy.Module | None:
    """Load an optimized DSPy module from disk.

    Args:
        prompt_name: Name of the prompt (e.g., 'deployer_fix_error')
        optimized_dir: Base directory containing versioned optimized prompts
        version: Version to load (e.g., 'v1', 'latest')

    Returns:
        Loaded DSPy module, or None on failure (missing files,
        corrupted data, invalid signatures).
    """
    # Check cache first
    cache_key = f"{prompt_name}:{version}"
    cached = _module_cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit for {cache_key}")
        return cached

    # Resolve version
    resolved_version = resolve_version(optimized_dir, version)
    if resolved_version is None:
        logger.error(f"Could not resolve version '{version}' in {optimized_dir}")
        return None

    # Update cache key with resolved version
    cache_key = f"{prompt_name}:{resolved_version}"
    cached = _module_cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit for {cache_key} (after version resolution)")
        return cached

    # Build path to module file
    version_dir = optimized_dir / resolved_version
    module_file = version_dir / f"{prompt_name}.dspy.json"

    if not module_file.exists():
        logger.error(f"DSPy module file not found: {module_file}")
        return None

    # Load the module
    try:
        logger.info(f"Loading DSPy module: {module_file}")

        # Get the signature class for this prompt
        signature_class = get_signature(prompt_name)

        # Create a Predict module with the signature
        module = dspy.Predict(signature_class)

        # Load the optimized state
        with open(module_file, 'r') as f:
            state = json.load(f)

        # Load demonstrations if present
        if 'demos' in state:
            module.demos = state['demos']
            logger.debug(f"Loaded {len(module.demos)} demonstrations")

        # Restore optimized instruction if present (COPRO / MIPROv2 artifact)
        if state.get('optimized_instruction'):
            module.signature = module.signature.with_instructions(state['optimized_instruction'])
            logger.debug(
                f"Restored optimized instruction ({len(state['optimized_instruction'])} chars)")

        # Store in cache
        _module_cache.set(cache_key, module)
        logger.debug(f"Cached module as {cache_key}")

        # Also cache under the original version key if it differs
        if version != resolved_version:
            _module_cache.set(f"{prompt_name}:{version}", module)

        return module

    except json.JSONDecodeError as e:
        logger.error(f"Corrupted DSPy module file {module_file}: {e}")
        return None
    except KeyError as e:
        logger.error(f"Invalid DSPy signature for '{prompt_name}': {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to load DSPy module from {module_file}: {e}")
        return None
