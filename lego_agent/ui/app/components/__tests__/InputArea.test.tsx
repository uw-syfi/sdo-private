import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { InputArea } from '../InputArea';
import '@testing-library/jest-dom';

describe('InputArea', () => {
  const mockOnSendPrompt = jest.fn();
  const mockOnSendAnswers = jest.fn();
  const mockOnStop = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders input for prompt when no pending questions', () => {
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
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
        onStop={mockOnStop}
        pendingQuestions={null}
        status="connected"
      />
    );

    const input = screen.getByPlaceholderText('Enter your prompt here...');
    fireEvent.change(input, { target: { value: 'test prompt' } });
    fireEvent.click(screen.getByText('Run'));

    expect(mockOnSendPrompt).toHaveBeenCalledWith('test prompt');
  });

  it('renders Stop button when running', () => {
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
        pendingQuestions={null}
        status="running"
      />
    );

    expect(screen.getByText('Stop')).toBeInTheDocument();
    expect(screen.queryByText('Run')).not.toBeInTheDocument();
    
    fireEvent.click(screen.getByText('Stop'));
    expect(mockOnStop).toHaveBeenCalled();
  });

  it('renders inputs for answers when pending questions exist', () => {
    const questions = ['Q1?', 'Q2?'];
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
        pendingQuestions={questions}
        status="connected"
      />
    );

    expect(screen.getByText('Clarification Needed:')).toBeInTheDocument();
    expect(screen.getByText('Q1?')).toBeInTheDocument();
    expect(screen.getByText('Q2?')).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText('Your answer...')).toHaveLength(2);
    expect(screen.getByText('Stop')).toBeInTheDocument();
  });

  it('calls onSendAnswers when submitting answers', () => {
    const questions = ['Q1?'];
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
        pendingQuestions={questions}
        status="connected"
      />
    );

    const input = screen.getByPlaceholderText('Your answer...');
    fireEvent.change(input, { target: { value: 'Answer 1' } });
    fireEvent.click(screen.getByText('Submit Answers'));

    expect(mockOnSendAnswers).toHaveBeenCalledWith(['Answer 1']);
  });

  it('calls onStop when clicking stop during questions', () => {
    const questions = ['Q1?'];
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
        pendingQuestions={questions}
        status="connected"
      />
    );

    fireEvent.click(screen.getByText('Stop'));
    expect(mockOnStop).toHaveBeenCalled();
  });
});
