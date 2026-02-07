'use client';

import { useLegoAgent } from './hooks/useLegoAgent';
import { TerminalLog } from './components/TerminalLog';
import { InputArea } from './components/InputArea';
import { TerminalSquare } from 'lucide-react';

export default function Home() {
  const { logs, status, pendingQuestions, sendPrompt, sendAnswers } = useLegoAgent();

  return (
    <main className="flex h-screen flex-col bg-black text-white font-sans">
      {/* Header */}
      <header className="h-14 border-b border-zinc-800 flex items-center px-4 bg-zinc-950 shrink-0">
        <TerminalSquare className="text-blue-500 mr-2" />
        <h1 className="font-bold text-lg tracking-tight">LegoAgent UI</h1>
        <div className="ml-auto flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${status === 'connected' || status === 'running' ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="text-xs text-zinc-500 uppercase">{status}</span>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden relative">
        <TerminalLog logs={logs} />
      </div>

      {/* Input Area */}
      <div className="shrink-0">
        <InputArea 
            onSendPrompt={sendPrompt} 
            onSendAnswers={sendAnswers} 
            pendingQuestions={pendingQuestions}
            status={status}
        />
      </div>
    </main>
  );
}
