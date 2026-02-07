import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { InputArea } from '../InputArea';
import '@testing-library/jest-dom';

describe('InputArea', () => {
  const mockOnSendPrompt = jest.fn();
  const mockOnSendAnswers = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders input for prompt when no pending questions', () => {
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        pendingQuestions={null}
        status="connected"
      />
    );

    const input = screen.getByPlaceholderText('Enter your prompt here...');
    expect(input).toBeInTheDocument();
    expect(screen.getByText('Run')).toBeInTheDocument();
  });

  it('calls onSendPrompt when submitting prompt', () => {
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        pendingQuestions={null}
        status="connected"
      />
    );

    const input = screen.getByPlaceholderText('Enter your prompt here...');
    fireEvent.change(input, { target: { value: 'test prompt' } });
    fireEvent.click(screen.getByText('Run'));

    expect(mockOnSendPrompt).toHaveBeenCalledWith('test prompt');
  });

  it('renders inputs for answers when pending questions exist', () => {
    const questions = ['Q1?', 'Q2?'];
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        pendingQuestions={questions}
        status="connected"
      />
    );

    expect(screen.getByText('Clarification Needed:')).toBeInTheDocument();
    expect(screen.getByText('Q1?')).toBeInTheDocument();
    expect(screen.getByText('Q2?')).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText('Your answer...')).toHaveLength(2);
  });

  it('calls onSendAnswers when submitting answers', () => {
    const questions = ['Q1?'];
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        pendingQuestions={questions}
        status="connected"
      />
    );

    const input = screen.getByPlaceholderText('Your answer...');
    fireEvent.change(input, { target: { value: 'Answer 1' } });
    fireEvent.click(screen.getByText('Submit Answers'));

    expect(mockOnSendAnswers).toHaveBeenCalledWith(['Answer 1']);
  });
});
