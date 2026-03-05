from pathlib import Path

from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.prompts import get_loader


def create_system_prompt(platform: str) -> str:
    """Create the system prompt for deployment script generation."""
    # Note: repo_path is not available here, so we don't pass it.
    # The default template doesn't need it.
    # If the seed template needs it, it will fail unless we provide it.
    # However, create_system_prompt is called without repo_path info in deployer.py
    return get_loader().render("deployer/system.jinja2", platform=platform, repo_path=".")


def analyze_repository(
    repo_path: Path,
    filesystem: FileSystemInterface | None = None,
) -> str:
    """Analyze repository structure and return context string.

    Args:
        repo_path: Path to the repository to analyze.
        filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.

    Returns:
        str: Context string describing the repository structure.
    """
    if filesystem is None:
        filesystem = RealFilesystem()

    context_parts = []

    # Check for common deployment files
    if filesystem.exists(repo_path / ".sds" / "code_analysis.md"):
        context_parts.append("- Found code analysis: .sds/code_analysis.md")
    if filesystem.exists(repo_path / ".sds" / "deployment_issues.md"):
        context_parts.append("- Found deployment issues report: .sds/deployment_issues.md")

    if filesystem.exists(repo_path / "docker-compose.yml"):
        context_parts.append("- Found docker-compose.yml (Docker Compose deployment)")
    if filesystem.exists(repo_path / "docker-compose.yaml"):
        context_parts.append("- Found docker-compose.yaml (Docker Compose deployment)")
    if filesystem.exists(repo_path / "Dockerfile"):
        context_parts.append("- Found Dockerfile (Docker-based application)")
    if filesystem.exists(repo_path / "k8s") or filesystem.exists(repo_path / "kubernetes"):
        context_parts.append("- Found Kubernetes manifests directory")
    if filesystem.exists(repo_path / "Makefile"):
        context_parts.append("- Found Makefile (may contain build/deploy targets)")

    # Check for common application files
    if filesystem.exists(repo_path / "package.json"):
        context_parts.append("- Found package.json (Node.js application)")
    if filesystem.exists(repo_path / "requirements.txt") or filesystem.exists(repo_path / "pyproject.toml"):
        context_parts.append("- Found Python dependencies (Python application)")
    if filesystem.exists(repo_path / "go.mod"):
        context_parts.append("- Found go.mod (Go application)")
    if filesystem.exists(repo_path / "Cargo.toml"):
        context_parts.append("- Found Cargo.toml (Rust application)")
    if filesystem.exists(repo_path / "pom.xml"):
        context_parts.append("- Found pom.xml (Java/Maven application)")

    # Check for README
    readme_files = filesystem.glob(repo_path, "README*")
    if readme_files:
        context_parts.append(f"- Found README file(s): {', '.join(f.name for f in readme_files)}")

    # Get repository name
    repo_name = repo_path.name
    context_parts.insert(0, f"Repository: {repo_name}")

    return (
        "\n".join(context_parts)
        if context_parts
        else "Repository structure analysis: No obvious deployment files found"
    )
