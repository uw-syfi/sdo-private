import React, { useState, useEffect, useRef } from 'react';
import { ChevronRight, Square, CornerDownLeft, Terminal } from 'lucide-react';
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
              const newPath = selected.endsWith('/') ? selected : selected + '/';
              setWorkDir(newPath);
              if (onListDirs) onListDirs(newPath);
          }
      }
  };

  const handleSuggestionClick = (path: string) => {
      const newPath = path.endsWith('/') ? path : path + '/';
      setWorkDir(newPath);
      if (onListDirs) onListDirs(newPath);
      
      workDirInputRef.current?.focus();
      setShowDirSuggestions(false);
  };

  useEffect(() => {
    if (pendingQuestions) {
        // eslint-disable-next-line
        setAnswers(new Array(pendingQuestions.length).fill(''));
    }
  }, [pendingQuestions]);

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
      <div className="border border-warning/50 rounded-none bg-warning/5 p-4 animate-in fade-in slide-in-from-bottom-2 font-mono">
        <div className="flex items-center gap-2 mb-4 text-warning">
             <span className="font-bold text-xs uppercase tracking-wide">&gt;&gt; Clarification Required</span>
        </div>
        
        <form onSubmit={handleSubmit} className="space-y-4">
            {pendingQuestions.map((q, i) => (
                <div key={i} className="space-y-2">
                    <label className="block text-xs font-bold text-foreground/90 text-warning/80">{q}</label>
                    <div className="flex items-center gap-2 text-warning">
                        <ChevronRight className="w-4 h-4" />
                        <input
                            type="text"
                            value={answers[i] || ''}
                            onChange={(e) => {
                                const newAns = [...answers];
                                newAns[i] = e.target.value;
                                setAnswers(newAns);
                            }}
                            className="flex-1 bg-transparent border-b border-warning/30 px-0 py-1 text-sm focus:outline-none focus:border-warning transition-colors text-foreground"
                            placeholder="Type answer..."
                            autoFocus={i === 0}
                        />
                    </div>
                </div>
            ))}
            <div className="flex gap-3 pt-2">
                <button
                  type="submit"
                  className="bg-warning/10 text-warning border border-warning/50 hover:bg-warning/20 font-bold py-1 px-4 text-xs uppercase tracking-wider transition-all"
                >
                  Submit Answers
                </button>
                <button
                  type="button"
                  onClick={onStop}
                  className="text-muted-foreground hover:text-foreground font-bold py-1 px-4 text-xs uppercase tracking-wider transition-all"
                >
                  Cancel
                </button>
            </div>
        </form>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="relative font-mono">
      <div className={cn(
          "bg-background border-t border-border p-4 transition-all duration-200"
      )}>
        {/* Working Directory Line */}
        <div className="flex items-center gap-2 mb-2 text-xs text-muted-foreground relative z-20">
            <Terminal className="w-3 h-3" />
            <div className="relative w-full flex items-center">
                <span className="text-brand font-bold mr-2">cwd:</span>
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
                    className="bg-transparent w-full text-xs font-mono text-muted-foreground focus:outline-none focus:text-foreground placeholder:text-muted-foreground/30 transition-colors"
                    placeholder="/path/to/working/directory"
                    autoComplete="off"
                />
                {showDirSuggestions && dirOptions.length > 0 && (
                    <div 
                        ref={suggestionsRef}
                        className="absolute bottom-full left-0 w-full mb-1 bg-popover border border-border shadow-lg max-h-60 overflow-y-auto z-50"
                    >
                        {dirOptions.map((opt, i) => (
                            <button
                                key={opt}
                                type="button"
                                onMouseDown={(e) => e.preventDefault()}
                                onClick={() => handleSuggestionClick(opt)}
                                className={cn(
                                    "w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-muted transition-colors truncate block",
                                    i === selectedIndex && "bg-muted text-brand font-bold"
                                )}
                            >
                                {opt}
                            </button>
                        ))}
                    </div>
                )}
            </div>
        </div>

        {/* Input Line */}
        <div className="flex items-start gap-2">
             <ChevronRight className="w-4 h-4 text-brand mt-1 shrink-0 animate-pulse" />
             <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={status === 'running' ? "Agent is busy..." : "Enter command or instructions..."}
                rows={1}
                disabled={status === 'running'}
                className="w-full bg-transparent px-0 py-1 text-sm resize-none focus:outline-none disabled:opacity-50 min-h-[24px] max-h-[200px] text-foreground placeholder:text-muted-foreground/30 caret-brand"
            />
        </div>
        
        <div className="flex justify-end items-center mt-2 h-6">
            {status === 'running' ? (
                <button
                type="button"
                onClick={onStop}
                className="text-error hover:text-error/80 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider transition-colors"
                >
                <Square className="w-3 h-3 fill-current" /> Stop Process
                </button>
            ) : input.trim() ? (
                 <span className="text-[10px] text-muted-foreground flex items-center gap-1 animate-in fade-in">
                    <CornerDownLeft className="w-3 h-3" /> Return to execute
                </span>
            ) : null}
        </div>
      </div>
    </form>
  );
}
