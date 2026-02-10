import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
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

    const input = screen.getByPlaceholderText('Enter command or instructions...');
    expect(input).toBeInTheDocument();
    // No Run button anymore, uses Enter
    // expect(screen.getByText('Run')).toBeInTheDocument(); 
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

    const input = screen.getByPlaceholderText('Enter command or instructions...');
    fireEvent.change(input, { target: { value: 'test prompt' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', charCode: 13 });

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

    expect(screen.getByText('Stop Process')).toBeInTheDocument();
    expect(screen.queryByText('Return to execute')).not.toBeInTheDocument();
    
    fireEvent.click(screen.getByText('Stop Process'));
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

    expect(screen.getByText('Clarification Required', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('Q1?')).toBeInTheDocument();
    expect(screen.getByText('Q2?')).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText('Type answer...')).toHaveLength(2);
    expect(screen.getByText('Cancel')).toBeInTheDocument();
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

    const input = screen.getByPlaceholderText('Type answer...');
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

  it('supports arrow key navigation in directory suggestions (circular)', () => {
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
    const optionA = screen.getByText('/tmp/a');
    const optionB = screen.getByText('/tmp/b');
    const optionC = screen.getByText('/tmp/c');

    // Check active class on A (index 0)
    expect(optionA).toHaveClass('bg-muted');
    expect(optionB).not.toHaveClass('bg-muted');

    // Press Down -> index 1 (B)
    fireEvent.keyDown(dirInput, { key: 'ArrowDown' });
    expect(optionB).toHaveClass('bg-muted');

    // Press Down -> index 2 (C)
    fireEvent.keyDown(dirInput, { key: 'ArrowDown' });
    expect(optionC).toHaveClass('bg-muted');

    // Press Down (Circular) -> index 0 (A)
    fireEvent.keyDown(dirInput, { key: 'ArrowDown' });
    expect(optionA).toHaveClass('bg-muted');

    // Press Up (Circular) -> index 2 (C)
    fireEvent.keyDown(dirInput, { key: 'ArrowUp' });
    expect(optionC).toHaveClass('bg-muted');
    
    // Press Enter to select C
    fireEvent.keyDown(dirInput, { key: 'Enter' });
    
    // Should update value AND append slash for next level
    expect(dirInput).toHaveValue('/tmp/c/');
    expect(mockListDirs).toHaveBeenCalledWith('/tmp/c/');
  });

  it('selects suggestion on click', () => {
    const mockListDirs = jest.fn();
    const suggestions = ['/tmp/click'];
    
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
    
    const option = screen.getByText('/tmp/click');
    fireEvent.click(option);

    expect(dirInput).toHaveValue('/tmp/click/');
    expect(mockListDirs).toHaveBeenCalledWith('/tmp/click/');
    
    // Should maintain focus
    expect(dirInput).toHaveFocus();

    // Dropdown should be closed
    expect(screen.queryByRole('button', { name: '/tmp/click' })).not.toBeInTheDocument();
  });

  it('closes suggestions when clicking outside', () => {
    const mockListDirs = jest.fn();
    const suggestions = ['/tmp/foo'];
    
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
    
    expect(screen.getByText('/tmp/foo')).toBeInTheDocument();

    // Click outside (e.g., on body)
    fireEvent.mouseDown(document.body);

    expect(screen.queryByText('/tmp/foo')).not.toBeInTheDocument();
  });

  it('updates workDir when initialCwd prop changes', () => {
    const { rerender } = render(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
        pendingQuestions={null}
        status="connected"
        initialCwd="/initial/path"
      />
    );

    expect(screen.getByDisplayValue('/initial/path')).toBeInTheDocument();

    // Rerender with new prop
    rerender(
      <InputArea
        onSendPrompt={mockOnSendPrompt}
        onSendAnswers={mockOnSendAnswers}
        onStop={mockOnStop}
        pendingQuestions={null}
        status="connected"
        initialCwd="/new/path"
      />
    );

    expect(screen.getByDisplayValue('/new/path')).toBeInTheDocument();
  });

  it('does not crash or select if no suggestions available', () => {
      render(
        <InputArea
          onSendPrompt={mockOnSendPrompt}
          onSendAnswers={mockOnSendAnswers}
          onStop={mockOnStop}
          pendingQuestions={null}
          status="connected"
          initialCwd="/tmp/"
          dirOptions={[]}
        />
      );
  
      const dirInput = screen.getByDisplayValue('/tmp/');
      fireEvent.focus(dirInput);
      
      // Press Enter
      fireEvent.keyDown(dirInput, { key: 'Enter' });
      
      // Should not have changed
      expect(dirInput).toHaveValue('/tmp/');
    });
});
