export const CheckErrorsPlugin = async ({ project, client, $, directory, worktree }) => {
  return {
    "tool.execute.before": async (input, output) => {
      // Check if the tool is bash and the command is a git commit
      if (input.tool === "bash" && input.args.command && input.args.command.trim().startsWith("git commit")) {
        try {
          // Run the error check script
          await $`./scripts/check_errors.sh`
        } catch (e) {
          // If the script fails, throw an error to block the tool execution
          throw new Error(`Pre-commit check failed: ${e.message}\n${e.stdout}\n${e.stderr}`)
        }
      }
    }
  }
}
