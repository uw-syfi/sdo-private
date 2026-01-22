export const FormatCodePlugin = async ({ project, client, $, directory, worktree }) => {
  return {
    "tool.execute.before": async (input, output) => {
      // Check if the tool is bash and the command is a git commit
      if (input.tool === "bash" && input.args.command && input.args.command.trim().startsWith("git commit")) {
        try {
          // Run the format check script
          await $`./scripts/format_code.sh --check`
        } catch (e) {
          // If the script fails, throw an error to block the tool execution
          throw new Error(`Pre-commit formatting check failed: ${e.message}\n${e.stdout}\n${e.stderr}\nPlease run './scripts/format_code.sh' to format your code.`)
        }
      }
    }
  }
}
