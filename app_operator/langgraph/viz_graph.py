"""Command handlers for visualizing the LangGraph agent graph."""

import argparse
from pathlib import Path
from unittest.mock import MagicMock

from app_operator.config import Config
from app_operator.langgraph.graph import build_graph
from app_operator.logger import logger


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments specific to the 'viz-graph' command."""
    parser.add_argument(
        "--output",
        "-o",
        help="Output file path (e.g., graph.png, graph.mermaid). If not specified, prints Mermaid syntax to stdout.",
    )


def run_command(args: argparse.Namespace) -> int:
    """Execute the 'viz-graph' command logic."""
    try:
        # Create a dummy LLM that supports binding tools
        llm = MagicMock()
        llm.bind_tools.return_value = llm

        # Create default config
        config = Config()

        # Use current directory as dummy repo path
        repo_path = Path(".")

        graph = build_graph(llm=llm, repo_path=repo_path, config=config, health_check_interval=30)

        compiled_graph = graph.get_graph()

        if args.output:
            output_path = Path(args.output)
            suffix = output_path.suffix.lower()

            if suffix == ".png":
                logger.info("Generating PNG image (this requires internet access to mermaid.ink)...")
                png_data = compiled_graph.draw_mermaid_png()
                with open(output_path, "wb") as f:
                    f.write(png_data)
                logger.info(f"Graph saved to {output_path}")
            else:
                # Default to text/mermaid for other extensions
                mermaid_code = compiled_graph.draw_mermaid()
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(mermaid_code)
                logger.info(f"Graph saved to {output_path}")
        else:
            print(compiled_graph.draw_mermaid())

        return 0

    except (ImportError, OSError, ValueError, RuntimeError) as e:
        logger.error(f"Error visualizing graph: {e}")
        return 1
