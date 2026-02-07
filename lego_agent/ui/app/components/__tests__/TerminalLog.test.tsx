import React from 'react';
import { render, screen } from '@testing-library/react';
import { TerminalLog } from '../TerminalLog';
import { LogItem } from '../../types';
import '@testing-library/jest-dom';

// Mock scrollIntoView since it's not supported in JSDOM
window.HTMLElement.prototype.scrollIntoView = jest.fn();

describe('TerminalLog', () => {
  it('renders thinking logs', () => {
    const logs: LogItem[] = [{
      id: '1',
      timestamp: 123,
      event: { type: 'thinking', text: 'Thinking...' }
    }];

    render(<TerminalLog logs={logs} />);
    expect(screen.getByText('Thinking...')).toBeInTheDocument();
  });

  it('renders tool start logs', () => {
    const logs: LogItem[] = [{
      id: '1',
      timestamp: 123,
      event: { type: 'tool_start', name: 'read_file', input: 'test.txt' }
    }];

    render(<TerminalLog logs={logs} />);
    expect(screen.getByText('read_file', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('test.txt')).toBeInTheDocument();
  });

  it('renders log messages with correct colors', () => {
      const logs: LogItem[] = [{
          id: '1',
          timestamp: 123,
          event: { type: 'log', message: 'Error occurred', level: 'error' }
      }];
      
      const { container } = render(<TerminalLog logs={logs} />);
      expect(screen.getByText('Error occurred')).toBeInTheDocument();
      expect(container.querySelector('.text-destructive')).toBeInTheDocument();
  });
});
