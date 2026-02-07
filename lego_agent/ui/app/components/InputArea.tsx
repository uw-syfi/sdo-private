import React, { useState } from 'react';
import { Send, Play, Square } from 'lucide-react';

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

  // Initialize answers array when questions arrive
  React.useEffect(() => {
    if (pendingQuestions) {
        setAnswers(new Array(pendingQuestions.length).fill(''));
    }
  }, [pendingQuestions]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (pendingQuestions) {
        onSendAnswers(answers);
    } else {
        if (!input.trim()) return;
        onSendPrompt(input);
        setInput('');
    }
  };

  if (pendingQuestions) {
    return (
      <form onSubmit={handleSubmit} className="p-4 bg-zinc-900 border-t border-zinc-800">
        <h3 className="text-yellow-400 mb-2 font-bold">Clarification Needed:</h3>
        <div className="space-y-4">
            {pendingQuestions.map((q, i) => (
                <div key={i}>
                    <label className="block text-sm text-gray-400 mb-1">{q}</label>
                    <input
                        type="text"
                        value={answers[i] || ''}
                        onChange={(e) => {
                            const newAns = [...answers];
                            newAns[i] = e.target.value;
                            setAnswers(newAns);
                        }}
                        className="w-full bg-black border border-zinc-700 rounded p-2 text-white focus:outline-none focus:border-blue-500"
                        placeholder="Your answer..."
                        autoFocus={i === 0}
                    />
                </div>
            ))}
        </div>
        <div className="flex gap-2 mt-4">
            <button
              type="submit"
              className="flex-1 bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded flex items-center justify-center gap-2"
            >
              <Send size={16} /> Submit Answers
            </button>
            <button
              type="button"
              onClick={onStop}
              className="bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-4 rounded flex items-center justify-center gap-2"
            >
              <Square size={16} fill="currentColor" /> Stop
            </button>
        </div>
      </form>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="p-4 bg-zinc-900 border-t border-zinc-800 flex gap-2">
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder={status === 'running' ? "Agent is working..." : "Enter your prompt here..."}
        className="flex-1 bg-black border border-zinc-700 rounded p-2 text-white focus:outline-none focus:border-blue-500 disabled:opacity-50"
      />
      {status === 'running' ? (
        <button
          type="button"
          onClick={onStop}
          className="bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-4 rounded flex items-center justify-center gap-2 transition-colors"
        >
          <Square size={16} fill="currentColor" /> Stop
        </button>
      ) : (
        <button
          type="submit"
          disabled={!input.trim()}
          className="bg-blue-600 hover:bg-blue-700 disabled:bg-zinc-700 text-white font-bold py-2 px-4 rounded flex items-center justify-center gap-2 transition-colors"
        >
          <Play size={16} /> Run
        </button>
      )}
    </form>
  );
}
