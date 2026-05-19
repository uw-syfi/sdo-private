
describe('analyze-experiment skill', () => {

  // Test case for Step 1: Gather Context
  it('should be able to read experiment configuration and aggregated results', () => {
    // This test would check if the skill can correctly parse config.toml and results.json
    // For example, by mocking the file system with these files and asserting the skill extracts the correct data.
    // Placeholder for implementation
  });

  // Test case for Step 2: Analyze Trajectories (single run)
  it('should analyze a single experiment run trajectory', () => {
    // This test would involve providing a sample trajectory.json and checking if the skill
    // can correctly identify the number of deployment attempts, errors, and fixes.
    // Placeholder for implementation
  });

  // Test case for Step 2: Analyze Trajectories (multiple runs with subagents)
  it('should use subagents to analyze multiple runs in parallel', () => {
    // This test would verify that the skill spawns subagents with the correct prompts
    // when multiple experiment runs are provided.
    // Placeholder for implementation
  });

  // Test case for Step 3: Identify Patterns
  it('should identify common efficiency, success, and failure patterns', () => {
    // This test would provide trajectories exhibiting specific patterns (e.g., repeated errors, detours)
    // and assert that the skill correctly identifies and flags them.
    // Placeholder for implementation
  });

  // Test case for Step 4: Report Findings
  it('should generate a structured analysis report', () => {
    // This test would check if the final output from the skill is a well-structured report
    // containing the required sections (Summary, Timeline, Key Issues, etc.).
    // Placeholder for implementation
  });

  // Test case for Comparing Multiple Experiments
  it('should be able to compare two different experiment runs', () => {
    // This test would provide data for two experiments and check if the skill can
    // generate a side-by-side comparison of their metrics and outcomes.
    // Placeholder for implementation
  });

  // Test case for handling custom paths
  it('should follow user-provided paths for experiment data', () => {
    // The documentation mentions "Always follow user instructions for where to find data."
    // This test would provide a custom path and verify the skill attempts to read data from that location.
    // Placeholder for implementation
  });

  // Test case for Key Files to Read
  it('should know which key files to read for analysis', () => {
    // This test would check that for a given analysis task, the skill attempts to read the correct
    // files as listed in the "Key Files to Read" table (e.g., trajectory, code_analysis.md).
    // Placeholder for implementation
  });

});
