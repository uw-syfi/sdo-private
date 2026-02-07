import React from 'react';
import { render, screen } from '@testing-library/react';
import { InputArea } from '../InputArea';
import '@testing-library/jest-dom';

describe('InputArea CWD Label', () => {
  const mockOnSendPrompt = jest.fn();
  const mockOnSendAnswers = jest.fn();
  const mockOnStop = jest.fn();

  it('renders the Working Directory label', () => {
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
        pendingQuestions={null}
        status="connected"
        initialCwd="/tmp/test"
      />
    );

    // Verify the new label text is present
    expect(screen.getByText('Working Directory')).toBeInTheDocument();
    
    // Verify the CWD input is associated with the label (implicitly by proximity/context or explicit aria/htmlFor if we added it, 
    // but here just checking presence is enough to confirm the UI update)
    expect(screen.getByDisplayValue('/tmp/test')).toBeInTheDocument();
  });
});
