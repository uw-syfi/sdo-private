from pathlib import Path

from app_operator.prompts import get_loader


def create_system_prompt(platform: str) -> str:
    """Create the system prompt for deployment script generation."""
    # Note: repo_path is not available here, so we don't pass it.
    # The default template doesn't need it.
    # If the seed template needs it, it will fail unless we provide it.
    # However, create_system_prompt is called without repo_path info in deployer.py
    return get_loader().render(
        "deployer/system.jinja2", platform=platform, repo_path="."
    )


def analyze_repository(repo_path: Path) -> str:
    """Analyze repository structure and return context string."""
    context_parts = []

    # Check for common deployment files
    if (repo_path / ".sds" / "code_analysis.md").exists():
        context_parts.append("- Found code analysis: .sds/code_analysis.md")
    if (repo_path / ".sds" / "deployment_issues.md").exists():
        context_parts.append(
            "- Found deployment issues report: .sds/deployment_issues.md"
        )

    if (repo_path / "docker-compose.yml").exists():
        context_parts.append("- Found docker-compose.yml (Docker Compose deployment)")
    if (repo_path / "docker-compose.yaml").exists():
        context_parts.append("- Found docker-compose.yaml (Docker Compose deployment)")
    if (repo_path / "Dockerfile").exists():
        context_parts.append("- Found Dockerfile (Docker-based application)")
    if (repo_path / "k8s").exists() or (repo_path / "kubernetes").exists():
        context_parts.append("- Found Kubernetes manifests directory")
    if (repo_path / "Makefile").exists():
        context_parts.append("- Found Makefile (may contain build/deploy targets)")

    # Check for common application files
    if (repo_path / "package.json").exists():
        context_parts.append("- Found package.json (Node.js application)")
    if (repo_path / "requirements.txt").exists() or (
        repo_path / "pyproject.toml"
    ).exists():
        context_parts.append("- Found Python dependencies (Python application)")
    if (repo_path / "go.mod").exists():
        context_parts.append("- Found go.mod (Go application)")
    if (repo_path / "Cargo.toml").exists():
        context_parts.append("- Found Cargo.toml (Rust application)")
    if (repo_path / "pom.xml").exists():
        context_parts.append("- Found pom.xml (Java/Maven application)")

    # Check for README
    readme_files = list(repo_path.glob("README*"))
    if readme_files:
        context_parts.append(
            f"- Found README file(s): {', '.join(f.name for f in readme_files)}"
        )

    # Get repository name
    repo_name = repo_path.name
    context_parts.insert(0, f"Repository: {repo_name}")

    return (
        "\n".join(context_parts)
        if context_parts
        else "Repository structure analysis: No obvious deployment files found"
    )
