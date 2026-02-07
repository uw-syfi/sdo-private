import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { InputArea } from '../InputArea';
import '@testing-library/jest-dom';

describe('InputArea Suggestions Esc Key', () => {
  const mockOnSendPrompt = jest.fn();
  const mockOnSendAnswers = jest.fn();
  const mockOnStop = jest.fn();
  const mockListDirs = jest.fn();

  it('closes suggestions when Esc is pressed', () => {
    const suggestions = ['/tmp/foo', '/tmp/bar'];
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
        pendingQuestions={null}
        status="connected"
        initialCwd="/tmp/"
        dirOptions={suggestions}
        onListDirs={mockListDirs}
      />
    );

    const dirInput = screen.getByDisplayValue('/tmp/');
    fireEvent.focus(dirInput);

    // Suggestions should be visible
    expect(screen.getByText('/tmp/foo')).toBeInTheDocument();

    // Press Esc
    fireEvent.keyDown(dirInput, { key: 'Escape' });

    // Suggestions should be hidden
    expect(screen.queryByText('/tmp/foo')).not.toBeInTheDocument();
  });
});
