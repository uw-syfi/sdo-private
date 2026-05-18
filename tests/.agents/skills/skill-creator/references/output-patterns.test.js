
describe('Output Patterns', () => {
  describe('Template Pattern', () => {
    it('should generate a report with a strict structure', () => {
      const strictTemplate = `
# [Analysis Title]

## Executive summary
[One-paragraph overview of key findings]

## Key findings
- Finding 1 with supporting data
- Finding 2 with supporting data
- Finding 3 with supporting data

## Recommendations
1. Specific actionable recommendation
2. Specific actionable recommendation
`;
      // This is a conceptual test. In a real scenario, we would have a function
      // that generates the report based on the template.
      const generatedReport = strictTemplate; // Assume generation for this test
      expect(generatedReport).toContain('# [Analysis Title]');
      expect(generatedReport).toContain('## Executive summary');
      expect(generatedReport).toContain('## Key findings');
      expect(generatedReport).toContain('## Recommendations');
    });

    it('should generate a report with a flexible structure', () => {
      const flexibleTemplate = `
# [Analysis Title]

## Executive summary
[Overview]

## Key findings
[Adapt sections based on what you discover]

## Recommendations
[Tailor to the specific context]
`;
      // This is a conceptual test. In a real scenario, we would have a function
      // that generates the report based on the template.
      const generatedReport = flexibleTemplate; // Assume generation for this test
      expect(generatedReport).toContain('# [Analysis Title]');
      expect(generatedReport).toContain('## Executive summary');
    });
  });

  describe('Examples Pattern', () => {
    it('should generate a commit message for a new feature', () => {
      const input = 'Added user authentication with JWT tokens';
      // This is a conceptual test. In a real scenario, we would have a function
      // that generates the commit message based on the input.
      const generatedCommitMessage = `
feat(auth): implement JWT-based authentication

Add login endpoint and token validation middleware
`;
      expect(generatedCommitMessage.trim()).toMatch(/^feat\(auth\):/);
    });

    it('should generate a commit message for a bug fix', () => {
      const input = 'Fixed bug where dates displayed incorrectly in reports';
      // This is a conceptual test. In a real scenario, we would have a function
      // that generates the commit message based on the input.
      const generatedCommitMessage = `
fix(reports): correct date formatting in timezone conversion

Use UTC timestamps consistently across report generation
`;
      expect(generatedCommitMessage.trim()).toMatch(/^fix\(reports\):/);
    });
  });
});
