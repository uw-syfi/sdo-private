import React, { useState, useEffect, useRef } from 'react';
import { Send, Play, Square, Folder } from 'lucide-react';
import { cn } from '@/lib/utils';

interface InputAreaProps {
  onSendPrompt: (prompt: string, workDir: string) => void;
  onSendAnswers: (answers: string[]) => void;
  onStop: () => void;
  pendingQuestions: string[] | null;
  status: string;
  initialCwd: string;
  dirOptions?: string[];
  onListDirs?: (path: string) => void;
}

export function InputArea({ 
    onSendPrompt, 
    onSendAnswers, 
    onStop, 
    pendingQuestions, 
    status, 
    initialCwd,
    dirOptions = [],
    onListDirs
}: InputAreaProps) {
  const [input, setInput] = useState('');
  const [workDir, setWorkDir] = useState(initialCwd);
  const [showDirSuggestions, setShowDirSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [answers, setAnswers] = useState<string[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const workDirInputRef = useRef<HTMLInputElement>(null);
  const suggestionsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (initialCwd && initialCwd !== '.') {
      setWorkDir(initialCwd);
    }
  }, [initialCwd]);

  // Reset selected index when options change
  useEffect(() => {
      setSelectedIndex(0);
  }, [dirOptions]);

  // Scroll selected item into view
  useEffect(() => {
    if (suggestionsRef.current && showDirSuggestions) {
        const selectedElement = suggestionsRef.current.children[selectedIndex] as HTMLElement;
        if (selectedElement) {
            const container = suggestionsRef.current;
            const itemTop = selectedElement.offsetTop;
            const itemBottom = itemTop + selectedElement.offsetHeight;
            const containerTop = container.scrollTop;
            const containerBottom = containerTop + container.offsetHeight;

            if (itemTop < containerTop) {
                container.scrollTop = itemTop;
            } else if (itemBottom > containerBottom) {
                container.scrollTop = itemBottom - container.offsetHeight;
            }
        }
    }
  }, [selectedIndex, showDirSuggestions, dirOptions]);

  // Hide suggestions when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
          workDirInputRef.current && 
          !workDirInputRef.current.contains(event.target as Node) &&
          suggestionsRef.current && 
          !suggestionsRef.current.contains(event.target as Node)
      ) {
        setShowDirSuggestions(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, []);

  const handleWorkDirChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      setWorkDir(val);
      if (onListDirs) {
          onListDirs(val);
          setShowDirSuggestions(true);
      }
  };

  const handleWorkDirKeyDown = (e: React.KeyboardEvent) => {
      if (dirOptions.length === 0 || !showDirSuggestions) {
          // If no suggestions, maybe Enter/Tab should do something else?
          // For now, let default happen or handled elsewhere.
          return;
      }

      if (e.key === 'ArrowDown') {
          e.preventDefault();
          setSelectedIndex(prev => (prev + 1) % dirOptions.length);
      } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          setSelectedIndex(prev => (prev - 1 + dirOptions.length) % dirOptions.length);
      } else if (e.key === 'Tab' || e.key === 'Enter') {
          e.preventDefault();
          const selected = dirOptions[selectedIndex];
          if (selected) {
              // Append / to trigger listing of children
              const newPath = selected.endsWith('/') ? selected : selected + '/';
              setWorkDir(newPath);
              // Keep suggestions open for next level
              // setShowDirSuggestions(false); 
              if (onListDirs) onListDirs(newPath);
          }
      }
  };

  const handleSuggestionClick = (path: string) => {
      const newPath = path.endsWith('/') ? path : path + '/';
      setWorkDir(newPath);
      if (onListDirs) onListDirs(newPath);
      
      // Focus first, then hide suggestions to ensure onFocus doesn't re-open them immediately
      workDirInputRef.current?.focus();
      setShowDirSuggestions(false);
  };

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
        onSendPrompt(input, workDir);
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
          "bg-card border border-input shadow-sm rounded-xl overflow-visible transition-all duration-200 relative",
          "focus-within:ring-2 focus-within:ring-ring focus-within:border-input"
      )}>
        <div className="flex items-center gap-2 px-4 py-2 bg-muted/30 border-b border-border relative z-20">
            <Folder className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
            <div className="relative w-full">
                <input 
                    ref={workDirInputRef}
                    type="text"
                    value={workDir}
                    onChange={handleWorkDirChange}
                    onFocus={() => {
                        if (onListDirs) onListDirs(workDir);
                        setShowDirSuggestions(true);
                    }}
                    onKeyDown={handleWorkDirKeyDown}
                    className="bg-transparent w-full text-xs font-mono text-muted-foreground focus:outline-none placeholder:text-muted-foreground/50"
                    placeholder="Working Directory (absolute path)"
                    autoComplete="off"
                />
                {showDirSuggestions && dirOptions.length > 0 && (
                    <div 
                        ref={suggestionsRef}
                        className="absolute bottom-full left-0 w-full mb-2 bg-popover border border-border rounded-lg shadow-lg max-h-80 overflow-y-auto z-50"
                    >
                        {dirOptions.map((opt, i) => (
                            <button
                                key={opt}
                                type="button"
                                onMouseDown={(e) => e.preventDefault()}
                                onClick={() => handleSuggestionClick(opt)}
                                className={cn(
                                    "w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-muted/50 transition-colors truncate block",
                                    i === selectedIndex && "bg-muted/50 text-brand-blue font-semibold"
                                )}
                            >
                                {opt}
                            </button>
                        ))}
                    </div>
                )}
            </div>
        </div>

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
