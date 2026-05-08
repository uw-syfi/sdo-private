import React from 'react';
import { render, screen } from '@testing-library/react';
import { QueueDebugPanel } from '../QueueDebugPanel';
import '@testing-library/jest-dom';

describe('QueueDebugPanel', () => {
  it('shows a placeholder before any snapshot arrives', () => {
    render(<QueueDebugPanel snapshot={null} />);

    expect(screen.getByText('Queue snapshots will appear here when a run starts.')).toBeInTheDocument();
  });

  it('renders stage queue contents', () => {
    render(
      <QueueDebugPanel
        snapshot={{
          max_concurrent_workers: 4,
          stages: [
            {
              name: 'agent_0',
              input_queue: 'q0',
              output_queue: 'q1',
              total: 2,
              active_total: 1,
              truncated: false,
              active_truncated: false,
              active_tasks: [{ id: 't0', type: 'start', label: 'README scan' }],
              tasks: [
                { id: 't1', type: 'start', label: 'README.md' },
                { id: 't2', type: 'start', label: 'docs/README.md' },
              ],
            },
          ],
        }}
      />
    );

    expect(screen.getByText('agent_0')).toBeInTheDocument();
    expect(screen.getByText('README.md')).toBeInTheDocument();
    expect(screen.getByText(/2\s+waiting/)).toBeInTheDocument();
    expect(screen.getByText(/1\s+active/)).toBeInTheDocument();
    expect(screen.getByText('Cap 4')).toBeInTheDocument();
  });
});