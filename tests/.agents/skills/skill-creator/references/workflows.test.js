describe('Workflow Patterns', () => {
  describe('Sequential Workflows', () => {
    it('should follow the steps in order to fill a PDF form', () => {
      const steps = [
        'analyze_form.py',
        'fields.json',
        'validate_fields.py',
        'fill_form.py',
        'verify_output.py'
      ];
      const executedSteps = [];

      const runStep = (step) => {
        executedSteps.push(step);
      };

      steps.forEach(runStep);

      expect(executedSteps).toEqual(steps);
    });
  });

  describe('Conditional Workflows', () => {
    const creationWorkflow = jest.fn();
    const editingWorkflow = jest.fn();

    const runWorkflow = (modificationType) => {
      if (modificationType === 'Creating new content') {
        creationWorkflow();
      } else if (modificationType === 'Editing existing content') {
        editingWorkflow();
      }
    };

    beforeEach(() => {
      creationWorkflow.mockClear();
      editingWorkflow.mockClear();
    });

    it('should follow the "Creation workflow" for creating new content', () => {
      runWorkflow('Creating new content');
      expect(creationWorkflow).toHaveBeenCalled();
      expect(editingWorkflow).not.toHaveBeenCalled();
    });

    it('should follow the "Editing workflow" for editing existing content', () => {
      runWorkflow('Editing existing content');
      expect(editingWorkflow).toHaveBeenCalled();
      expect(creationWorkflow).not.toHaveBeenCalled();
    });
  });
});