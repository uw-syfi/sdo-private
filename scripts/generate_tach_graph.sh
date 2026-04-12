#!/bin/bash
# Generate a module dependency graph from tach.toml.
# Outputs:  docs/assets/tach_module_graph.dot
#           docs/assets/tach_module_graph.png
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

DOT_FILE="docs/assets/tach_module_graph.dot"
PNG_FILE="docs/assets/tach_module_graph.png"

if ! command -v dot >/dev/null 2>&1; then
    echo "Error: 'dot' (graphviz) is not installed."
    exit 1
fi

python3 - "$DOT_FILE" <<'PYEOF'
import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

dot_path = sys.argv[1]

with open("tach.toml", "rb") as f:
    config = tomllib.load(f)

modules = config.get("modules", [])

lines = [
    'digraph tach_modules {',
    '    rankdir=TB;',
    '    node [shape=box, style=filled, fillcolor="#e8f4f8", fontname="Helvetica"];',
    '    edge [color="#666666"];',
    '',
]

for mod in modules:
    path = mod["path"]
    for dep in mod.get("depends_on", []):
        lines.append(f'    "{path}" -> "{dep}";')

lines.append('}')

with open(dot_path, "w") as f:
    f.write("\n".join(lines) + "\n")

print(f"Wrote {dot_path}")
PYEOF

dot -Tpng "$DOT_FILE" -o "$PNG_FILE"
echo "Wrote $PNG_FILE"
