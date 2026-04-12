import json
import sys


def truncate_content(content, max_lines=5, max_chars=500):
    lines = content.splitlines()
    if len(lines) > max_lines * 2:
        return "\n".join(
            lines[:max_lines] + ["... (truncated) ..."] + lines[-max_lines:]
        )
    if len(content) > max_chars:
        return (
            content[: max_chars / 2]
            + " ... (truncated) ... "
            + content[-max_chars / 2:]
        )
    return content


def process_line(line):
    try:
        data = json.loads(line)
        msg_type = data.get("type")

        if msg_type == "message":
            role = data.get("role")
            content = data.get("content", "")
            if role == "assistant":
                # For test, just print content. In real agent, handle streaming/deltas
                sys.stdout.write(content)
                sys.stdout.flush()

        elif msg_type == "tool_use":
            tool_name = data.get("tool_name")
            params = data.get("parameters")
            print(f"\n\033[94m[Tool Use] {tool_name} {params}\033[0m")

        elif msg_type == "tool_result":
            output = data.get("output", "")
            truncated = truncate_content(output)
            print(f"\033[92m[Tool Result] {truncated}\033[0m")

    except json.JSONDecodeError:
        print(f"Raw: {line}")


def main():
    with open("example_stream_json.txt") as f:
        for line in f:
            if not line.strip():
                continue
            # The example file has "00001| " prefix from the read tool, I need to strip it if I used the output directly,  # noqa: E501
            # but here I am reading the file on disk.
            # Wait, the example content I read previously had line numbers because of cat -n format from the Read tool.
            # The actual file on disk probably doesn't have "00001| ".
            # I should double check the file content without line numbers.

    # Actually, I'll just rely on the read I did before.
    # The read output I saw earlier had line numbers because the tool adds them.
    # The actual file `example_stream_json.txt` should be clean JSON lines.

    with open("example_stream_json.txt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Skip non-json lines if any (like YOLO mode lines in the example output?)
            if line.startswith("{"):
                process_line(line)
            else:
                print(f"INFO: {line}")


if __name__ == "__main__":
    main()
