import React, { useState, useEffect, useRef } from 'react';
import { Send, Play, Square } from 'lucide-react';
import { cn } from '@/lib/utils';

interface InputAreaProps {
  onSendPrompt: (prompt: string) => void;
  onSendAnswers: (answers: string[]) => void;
  onStop: () => void;
  pendingQuestions: string[] | null;
  status: string;
}

export function InputArea({ onSendPrompt, onSendAnswers, onStop, pendingQuestions, status }: InputAreaProps) {
  const [input, setInput] = useState('');
  const [answers, setAnswers] = useState<string[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Initialize answers array when questions arrive
  useEffect(() => {
    if (pendingQuestions) {
        // eslint-disable-next-line
        setAnswers(new Array(pendingQuestions.length).fill(''));
    }
  }, [pendingQuestions]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = textareaRef.current.scrollHeight + 'px';
    }
  }, [input]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (pendingQuestions) {
        onSendAnswers(answers);
    } else {
        if (!input.trim()) return;
        onSendPrompt(input);
        setInput('');
        if (textareaRef.current) textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  if (pendingQuestions) {
    return (
      <div className="bg-card border border-border rounded-xl shadow-lg p-6 animate-in fade-in slide-in-from-bottom-4">
        <div className="flex items-center gap-2 mb-4 text-amber-500">
             <div className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
             <h3 className="font-semibold text-sm uppercase tracking-wide">Clarification Needed</h3>
        </div>
        
        <form onSubmit={handleSubmit} className="space-y-5">
            {pendingQuestions.map((q, i) => (
                <div key={i} className="space-y-2">
                    <label className="block text-sm font-medium text-foreground/90">{q}</label>
                    <input
                        type="text"
                        value={answers[i] || ''}
                        onChange={(e) => {
                            const newAns = [...answers];
                            newAns[i] = e.target.value;
                            setAnswers(newAns);
                        }}
                        className="w-full bg-muted/50 border border-input rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-input transition-all"
                        placeholder="Type your answer..."
                        autoFocus={i === 0}
                    />
                </div>
            ))}
            <div className="flex gap-3 pt-2">
                <button
                  type="submit"
                  className="flex-1 bg-brand-blue text-white hover:opacity-90 font-medium py-2.5 px-4 rounded-lg flex items-center justify-center gap-2 text-sm transition-all"
                >
                  <Send className="w-4 h-4" /> Submit Answers
                </button>
                <button
                  type="button"
                  onClick={onStop}
                  className="bg-secondary text-secondary-foreground hover:bg-secondary/80 font-medium py-2.5 px-4 rounded-lg flex items-center justify-center gap-2 text-sm transition-all"
                >
                  <Square className="w-4 h-4 fill-current" /> Stop
                </button>
            </div>
        </form>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="relative group">
      <div className={cn(
          "bg-card border border-input shadow-sm rounded-xl overflow-hidden transition-all duration-200",
          "focus-within:ring-2 focus-within:ring-ring focus-within:border-input"
      )}>
        <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={status === 'running' ? "Agent is working..." : "Describe your task..."}
            rows={1}
            disabled={status === 'running'}
            className="w-full bg-transparent px-4 py-4 text-sm resize-none focus:outline-none disabled:opacity-50 min-h-[56px] max-h-[200px]"
        />
        
        <div className="flex justify-between items-center px-2 pb-2">
            <div className="px-2">
                <span className="text-[10px] text-muted-foreground hidden group-focus-within:inline-block">
                    Press Enter to send, Shift+Enter for new line
                </span>
            </div>
            {status === 'running' ? (
                <button
                type="button"
                onClick={onStop}
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90 h-9 px-4 rounded-lg flex items-center gap-2 text-sm font-medium transition-colors"
                >
                <Square className="w-3.5 h-3.5 fill-current" /> Stop
                </button>
            ) : (
                <button
                type="submit"
                disabled={!input.trim()}
                className="bg-brand-blue text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed h-9 px-4 rounded-lg flex items-center gap-2 text-sm font-medium transition-all"
                >
                <Play className="w-3.5 h-3.5 fill-current" /> Run
                </button>
            )}
        </div>
      </div>
    </form>
  );
}
