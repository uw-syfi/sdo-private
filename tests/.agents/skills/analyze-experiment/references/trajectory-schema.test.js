
describe('Trajectory JSON Schema', () => {
  // A sample valid trajectory object for testing
  const validTrajectory = {
    metadata: {
      repo_path: '/path/to/repo',
      start_time: '2023-10-27T10:00:00Z',
      end_time: '2023-10-27T10:30:00Z',
      agent_name: 'GeminiGenerationSession',
      status: 'completed',
      run_id: '20231027-100000',
      token_usage: {
        input_tokens: 100,
        output_tokens: 200,
        total_tokens: 300,
      },
    },
    calls: [
      {
        call_id: 1,
        phase: 'exploration',
        start_time: '2023-10-27T10:00:00Z',
        end_time: '2023-10-27T10:05:00Z',
        context: {},
      },
    ],
    exploration: [
      {
        call_id: 1,
        messages: [
          {
            role: 'user',
            content: 'Analyze the codebase.',
            timestamp: '2023-10-27T10:00:05Z',
          },
          {
            role: 'assistant',
            content: 'I have analyzed the codebase.',
            timestamp: '2023-10-27T10:01:00Z',
          },
          {
            role: 'tool_call',
            tool: 'bash',
            args: { command: 'ls -l' },
            stdout: 'total 0',
            stderr: '',
            exit_code: 0,
            timestamp: '2023-10-27T10:00:10Z',
          }
        ],
      },
    ],
    script_generation: [],
    deployment: [],
    monitoring: [],
    gemini_sessions: [],
  };

  describe('Top-Level Structure', () => {
    it('should have a metadata object', () => {
      expect(validTrajectory.metadata).toBeInstanceOf(Object);
    });

    it('should have a calls array', () => {
      expect(validTrajectory.calls).toBeInstanceOf(Array);
    });

    it('should have an exploration array', () => {
      expect(validTrajectory.exploration).toBeInstanceOf(Array);
    });

    it('should have a script_generation array', () => {
      expect(validTrajectory.script_generation).toBeInstanceOf(Array);
    });

    it('should have a deployment array', () => {
      expect(validTrajectory.deployment).toBeInstanceOf(Array);
    });

    it('should have a monitoring array', () => {
      expect(validTrajectory.monitoring).toBeInstanceOf(Array);
    });
  });

  describe('Metadata Object', () => {
    it('should have a string repo_path', () => {
      expect(typeof validTrajectory.metadata.repo_path).toBe('string');
    });

    it('should have a valid ISO datetime start_time', () => {
      expect(new Date(validTrajectory.metadata.start_time).toISOString()).toBe(validTrajectory.metadata.start_time);
    });

    it('should have a valid status', () => {
      const validStatuses = ['running', 'completed', 'failed', 'interrupted'];
      expect(validStatuses).toContain(validTrajectory.metadata.status);
    });

    it('should have token_usage object with number properties if it exists', () => {
        if (validTrajectory.metadata.token_usage) {
            expect(typeof validTrajectory.metadata.token_usage.input_tokens).toBe('number');
            expect(typeof validTrajectory.metadata.token_usage.output_tokens).toBe('number');
            expect(typeof validTrajectory.metadata.token_usage.total_tokens).toBe('number');
        }
    });
  });

  describe('Calls Array', () => {
    it('should contain objects with the correct properties', () => {
      const call = validTrajectory.calls[0];
      expect(typeof call.call_id).toBe('number');
      const validPhases = ['exploration', 'script_generation', 'deployment', 'monitoring'];
      expect(validPhases).toContain(call.phase);
      expect(new Date(call.start_time).toISOString()).toBe(call.start_time);
      if (call.end_time) {
        expect(new Date(call.end_time).toISOString()).toBe(call.end_time);
      }
      expect(call.context).toBeInstanceOf(Object);
    });
  });

  describe('ConversationEntry Object', () => {
    it('should have a number call_id', () => {
        const entry = validTrajectory.exploration[0];
        expect(typeof entry.call_id).toBe('number');
    });
    it('should have a messages array', () => {
        const entry = validTrajectory.exploration[0];
        expect(entry.messages).toBeInstanceOf(Array);
    });
  });

  describe('TrajectoryMessage Object', () => {
    it('should have a valid role', () => {
      const message = validTrajectory.exploration[0].messages[0];
      const validRoles = ['system', 'user', 'assistant', 'tool_call'];
      expect(validRoles).toContain(message.role);
    });

    it('should have a content string or be undefined', () => {
        const message = validTrajectory.exploration[0].messages[0];
        if (message.content) {
            expect(typeof message.content).toBe('string');
        }
    });

    it('should have a valid ISO datetime timestamp', () => {
        const message = validTrajectory.exploration[0].messages[0];
        expect(new Date(message.timestamp).toISOString()).toBe(message.timestamp);
    });

    it('should have tool-specific fields if role is tool_call', () => {
        const toolCallMessage = validTrajectory.exploration[0].messages[2];
        expect(toolCallMessage.role).toBe('tool_call');
        expect(typeof toolCallMessage.tool).toBe('string');
        expect(toolCallMessage.args).toBeInstanceOf(Object);
        expect(typeof toolCallMessage.stdout).toBe('string');
        expect(typeof toolCallMessage.stderr).toBe('string');
        expect(typeof toolCallMessage.exit_code).toBe('number');
    });
  });
});
