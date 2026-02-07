'use client';

import { useLegoAgent } from './hooks/useLegoAgent';
import { TerminalLog } from './components/TerminalLog';
import { InputArea } from './components/InputArea';
import { Terminal, Circle } from 'lucide-react';
import { cn } from '@/lib/utils';

export default function Home() {
  const { logs, status, pendingQuestions, sendPrompt, sendAnswers, stopAgent, cwd, dirOptions, listDirs, model, thinkingBudget } = useLegoAgent();

  return (
    <main className="flex h-screen flex-col font-mono bg-background text-foreground selection:bg-brand/30">
      {/* Header */}
      <header className="h-12 border-b border-border flex items-center px-4 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 shrink-0 z-10 sticky top-0 justify-between">
        <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 text-foreground/90 hover:text-foreground transition-colors">
                <Terminal className="w-4 h-4" />
                <span className="font-bold text-sm tracking-tight">LegoAgent</span>
            </div>
            {(status === 'disconnected' || status === 'connecting') && (
                <>
                    <div className="h-4 w-px bg-border mx-1" />
                    <div className="flex items-center gap-2">
                         <Circle className="w-2 h-2 fill-current text-error" />
                         <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{status}</span>
                    </div>
                </>
            )}
        </div>

        {/* Status Info */}
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
            {model && (
                <div className="flex items-center gap-1.5">
                    <span className="text-muted-foreground/60">model:</span>
                    <span className="font-bold text-foreground">{model}</span>
                </div>
            )}
            {thinkingBudget && (
                 <div className="flex items-center gap-1.5">
                    <span className="text-muted-foreground/60">thinking:</span>
                    <span className="font-bold text-foreground">{thinkingBudget} tok</span>
                </div>
            )}
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden relative">
        <div className="flex-1 w-full max-w-5xl mx-auto flex flex-col min-h-0">
            <TerminalLog logs={logs} />
        </div>
      </div>

      {/* Input Area */}
      <div className="shrink-0 bg-background border-t border-border">
         <div className="w-full max-w-5xl mx-auto">
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
