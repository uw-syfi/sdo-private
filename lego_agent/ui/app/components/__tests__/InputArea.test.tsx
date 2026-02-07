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
        initialCwd="/tmp/test"
      />
    );

    const input = screen.getByPlaceholderText('Describe your task...');
    expect(input).toBeInTheDocument();
    expect(screen.getByText('Run')).toBeInTheDocument();
    // Check for work dir input
    expect(screen.getByDisplayValue('/tmp/test')).toBeInTheDocument();
  });

  it('calls onSendPrompt when submitting prompt', () => {
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
        pendingQuestions={null}
        status="connected"
        initialCwd="."
      />
    );

    const input = screen.getByPlaceholderText('Describe your task...');
    fireEvent.change(input, { target: { value: 'test prompt' } });
    fireEvent.click(screen.getByText('Run'));

    expect(mockOnSendPrompt).toHaveBeenCalledWith('test prompt', '.');
  });

  it('renders Stop button when running', () => {
    render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
        pendingQuestions={null}
        status="running"
        initialCwd="."
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
        initialCwd="."
      />
    );

    expect(screen.getByText('Clarification Needed')).toBeInTheDocument();
    expect(screen.getByText('Q1?')).toBeInTheDocument();
    expect(screen.getByText('Q2?')).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText('Type your answer...')).toHaveLength(2);
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
        initialCwd="."
      />
    );

    const input = screen.getByPlaceholderText('Type your answer...');
    fireEvent.change(input, { target: { value: 'Answer 1' } });
    fireEvent.click(screen.getByText('Submit Answers'));

    expect(mockOnSendAnswers).toHaveBeenCalledWith(['Answer 1']);
  });

  it('shows directory suggestions and selects on tab', () => {
    const mockListDirs = jest.fn();
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
    
    // Suggestion dropdown should appear
    expect(screen.getByText('/tmp/foo')).toBeInTheDocument();
    
    // Type more
    fireEvent.change(dirInput, { target: { value: '/tmp/f' } });
    expect(mockListDirs).toHaveBeenCalledWith('/tmp/f');

    // Press Tab
    fireEvent.keyDown(dirInput, { key: 'Tab' });
    
    // Should update value and append slash
    expect(dirInput).toHaveValue('/tmp/foo/');
  });

  it('supports arrow key navigation in directory suggestions', () => {
    const mockListDirs = jest.fn();
    const suggestions = ['/tmp/a', '/tmp/b', '/tmp/c'];
    
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
    
    // Initial selection should be index 0 (/tmp/a)
    // Press Down Arrow -> index 1 (/tmp/b)
    fireEvent.keyDown(dirInput, { key: 'ArrowDown' });
    
    // Press Enter to select
    fireEvent.keyDown(dirInput, { key: 'Enter' });
    
    // Should update value AND append slash for next level
    expect(dirInput).toHaveValue('/tmp/b/');
    
    // Should trigger list dirs for next level
    expect(mockListDirs).toHaveBeenCalledWith('/tmp/b/');
  });
});
