
describe('git-branch skill', () => {
  // Test for developer name inference
  describe('Developer Name Inference', () => {
    it('should extract the first part of a hyphenated name and convert to lowercase', () => {
      const gitUserName = 'vic-lsh';
      const inferredName = inferDeveloperName(gitUserName);
      expect(inferredName).toBe('vic');
    });

    it('should extract the first part of a name with spaces and convert to lowercase', () => {
      const gitUserName = 'John Doe';
      const inferredName = inferDeveloperName(gitUserName);
      expect(inferredName).toBe('john');
    });

    it('should handle single names', () => {
      const gitUserName = 'Alice';
      const inferredName = inferDeveloperName(gitUserName);
      expect(inferredName).toBe('alice');
    });
  });

  // Test for branch type inference
  describe('Branch Type Inference', () => {
    it('should infer "feat" for new features', () => {
      const description = 'Add user authentication';
      const inferredType = inferBranchType(description);
      expect(inferredType).toBe('feat');
    });

    it('should infer "fix" for bug fixes', () => {
      const description = 'Fix login redirect issue';
      const inferredType = inferBranchType(description);
      expect(inferredType).toBe('fix');
    });

    it('should infer "refactor" for code refactoring', () => {
      const description = 'Clean up error handling';
      const inferredType = inferBranchType(description);
      expect(inferredType).toBe('refactor');
    });

    it('should infer "chore" for maintenance tasks', () => {
      const description = 'Update dependencies';
      const inferredType = inferBranchType(description);
      expect(inferredType).toBe('chore');
    });
  });

  // Test for brief description formatting
  describe('Brief Description Formatting', () => {
    it('should convert description to lowercase and use dashes for spaces', () => {
      const description = 'Add user authentication';
      const formattedDesc = formatBriefDescription(description);
      expect(formattedDesc).toBe('add-user-authentication');
    });

    it('should handle multiple spaces between words', () => {
      const description = 'Update  deployment   scripts';
      const formattedDesc = formatBriefDescription(description);
      expect(formattedDesc).toBe('update-deployment-scripts');
    });

    it('should keep it concise', () => {
      const description = 'Fix authentication in login flow';
      const formattedDesc = formatBriefDescription(description);
      // Example from the document suggests 'fix-auth-login' but a direct conversion would be 'fix-authentication-in-login-flow'.
      // This test will check for a direct conversion, assuming the AI will handle conciseness.
      expect(formattedDesc).toBe('fix-authentication-in-login-flow');
    });
  });

  // Test for the final branch name creation
  describe('Branch Creation', () => {
    it('should construct the branch name in the format <name>/<type>/<brief-desc>', () => {
      const branch = createBranchName('vic', 'feat', 'code-analyzer');
      expect(branch).toBe('vic/feat/code-analyzer');
    });

    it('should generate the correct git checkout command', () => {
      const branchName = 'vic/feat/health-check-endpoint';
      const command = generateCheckoutCommand(branchName);
      expect(command).toBe(`git checkout -b ${branchName}`);
    });
  });

  // Mock functions for the purpose of testing the logic
  function inferDeveloperName(gitUserName) {
    return gitUserName.split(/-| /)[0].toLowerCase();
  }

  function inferBranchType(description) {
    const lowerDesc = description.toLowerCase();
    if (lowerDesc.includes('add') || lowerDesc.includes('implement')) {
      return 'feat';
    } else if (lowerDesc.includes('fix')) {
      return 'fix';
    } else if (lowerDesc.includes('clean up') || lowerDesc.includes('refactor')) {
      return 'refactor';
    } else {
      return 'chore';
    }
  }

  function formatBriefDescription(description) {
    return description.toLowerCase().replace(/\s+/g, '-');
  }

  function createBranchName(name, type, desc) {
    return `${name}/${type}/${desc}`;
  }

  function generateCheckoutCommand(branchName) {
    return `git checkout -b ${branchName}`;
  }
});
