'use client';

import { useLegoAgent } from './hooks/useLegoAgent';
import { TerminalLog } from './components/TerminalLog';
import { InputArea } from './components/InputArea';
import { Bot } from 'lucide-react';

export default function Home() {
  const { logs, status, pendingQuestions, sendPrompt, sendAnswers, stopAgent, cwd, dirOptions, listDirs, model, thinkingBudget } = useLegoAgent();

  return (
    <main className="flex h-screen flex-col font-sans selection:bg-primary/20">
      {/* Header */}
      <header className="h-16 border-b border-border flex items-center px-6 bg-background/50 backdrop-blur-sm shrink-0 z-10 sticky top-0 justify-between">
        <div className="flex items-center gap-3">
            <div className="bg-primary/10 p-2 rounded-lg">
                <Bot className="text-primary w-5 h-5" />
            </div>
            <div>
                <h1 className="font-semibold text-sm leading-none">LegoAgent</h1>
                <div className="flex items-center gap-1.5 mt-1">
                    <span className={`h-1.5 w-1.5 rounded-full ${status === 'connected' || status === 'running' ? 'bg-emerald-500 shadow-emerald-500/50 shadow-sm' : 'bg-red-500'}`} />
                    <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">{status}</span>
                </div>
            </div>
        </div>

        {/* Model Info */}
        <div className="flex items-center gap-6 text-xs text-muted-foreground">
            {model && (
                <div className="flex items-center gap-2 px-3 py-1.5 bg-secondary/50 rounded-full border border-border/50">
                    <span className="font-medium text-foreground">Model</span>
                    <span className="font-mono">{model}</span>
                </div>
            )}
            <div className="flex items-center gap-2 px-3 py-1.5 bg-secondary/50 rounded-full border border-border/50">
                <span className="font-medium text-foreground">Thinking</span>
                <span className="font-mono">{thinkingBudget ? `${thinkingBudget} tok` : 'off'}</span>
            </div>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden relative">
        <div className="flex-1 w-full max-w-3xl mx-auto flex flex-col min-h-0">
            <TerminalLog logs={logs} />
        </div>
      </div>

      {/* Input Area */}
      <div className="shrink-0 p-4 pb-6 bg-background">
         <div className="w-full max-w-3xl mx-auto">
            <InputArea 
                onSendPrompt={sendPrompt} 
                onSendAnswers={sendAnswers}
                onStop={stopAgent} 
                pendingQuestions={pendingQuestions}
                status={status}
                initialCwd={cwd}
                dirOptions={dirOptions}
                onListDirs={listDirs}
            />
         </div>
      </div>
    </main>
  );
}
