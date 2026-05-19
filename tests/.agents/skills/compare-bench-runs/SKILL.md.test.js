
describe('compare-bench-runs skill', () => {

  describe('Prerequisites', () => {
    it('should verify the presence of required files in run directories', () => {
      // This test would check that for a given run directory, the necessary files 
      // like `diagnosis.md`, `mitigation.md`, `results_<iter_ts>.csv`, and `run.log` are present.
      // For example, it would assert that a call to a function that checks for these files returns true.
    });
  });

  describe('Step 1: Extract and Compare Results', () => {
    it('should correctly identify improved problems', () => {
      // Given mock data representing a run where a problem failed and another where it succeeded,
      // this test would assert that the output JSON correctly lists the problem under "improved problems".
    });

    it('should correctly identify regressed problems', () => {
      // Given mock data representing a run where a problem succeeded and another where it failed,
      // this test would assert that the output JSON correctly lists the problem under "regressed problems".
    });

    it('should correctly identify problems that succeeded in both runs with TTL changes', () => {
      // Given mock data where a problem succeeded in two different runs with different TTLs,
      // this test would assert that the output JSON lists the problem under "both-succeeded with TTL changes".
    });

    it('should correctly identify problems that failed in both runs', () => {
      // Given mock data where a problem failed in two different runs,
      // this test would assert that the output JSON correctly lists the problem under "both-failed".
    });

    it('should handle problems that only exist in one run', () => {
      // Given mock data with a problem present in only one of the two runs,
      // this test would assert that the output JSON correctly lists the problem under "problems only in one run".
    });

    it('should output a summary table to the user', () => {
      // This test would check if a function responsible for printing a summary table is called.
    });
  });

  describe('Step 2: Analyze Each Problem with Subagents', () => {
    it('should launch a subagent for each problem', () => {
      // This test would check that for a given number of problems, the skill launches an equal number of subagents.
      // It might involve checking the number of "run_in_background" calls.
    });

    it('should provide the correct information in the subagent prompt', () => {
      // This test would capture the prompt sent to a subagent and assert that it contains the correct problem name,
      // outcomes, TTLs, and file paths as described in the SKILL.md.
    });

    it('should instruct the subagent to write its analysis to a specific output file', () => {
      // This test would check that the subagent prompt includes the instruction to write the analysis
      // to a specific file, and that the file path is correctly formatted.
    });
  });

  describe('Step 3: Synthesize Findings', () => {
    it('should create a SYNTHESIS.md file', () => {
      // After running the synthesis step, this test would assert that a `SYNTHESIS.md` file exists in the output directory.
    });

    it('should include an overview table in the synthesis report', () => {
      // This test would read the content of `SYNTHESIS.md` and assert that it contains a markdown table with solve rates, median TTL, and total problems.
    });

    it('should include outcome change tables for improvements and regressions', () => {
      // This test would check the `SYNTHESIS.md` content for the presence of tables for "improvements" and "regressions".
    });

    it('should include a both-succeeded TTL table sorted by speedup percentage', () => {
      // This test would verify that `SYNTHESIS.md` contains a table for "both-succeeded" problems and that it is sorted by speedup percentage.
    });

    it('should include a both-failed table with impact assessment', () => {
      // This test would check for the presence of a "both-failed" table in `SYNTHESIS.md` and that it includes an impact assessment.
    });

    it('should include high-level takeaways', () => {
      // This test would parse `SYNTHESIS.md` to ensure that a "High-level takeaways" section is present.
    });

    it('should include a judge effectiveness analysis', () => {
      // This test would assert that a "Judge effectiveness analysis" section is included in `SYNTHESIS.md`.
    });

    it('should include actionable recommendations', () => {
      // This test would check for an "Actionable recommendations" section in `SYNTHESIS.md`.
    });

    it('should include a per-problem file index', () => {
      // This test would ensure that `SYNTHESIS.md` contains a file index with links to individual analysis files.
    });
  });

  describe('Prioritization based on user request', () => {
    it('should focus on outcome-changed problems when the user asks "Why did X regress?"', () => {
      // This test would simulate a user request about a regression and assert that the skill prioritizes the analysis of "regressed problems".
    });

    it('should focus on both-succeeded TTL changes and regressions when the user asks "Was LTM helpful?"', () => {
      // This test would simulate a user request about LTM and check that the analysis focuses on TTL changes in "both-succeeded" problems and regressions.
    });

    it('should focus on judge behavior across all problems when the user asks "How did the judge do?"', () => {
      // This test would simulate a user request about the judge and assert that the analysis includes judge behavior from all types of problem outcomes.
    });

    it('should focus on both-failed problems when the user asks "What problems are hardest?"', () => {
      // This test would simulate a user request about the hardest problems and check that the analysis focuses on "both-failed" problems.
    });
  });
});
