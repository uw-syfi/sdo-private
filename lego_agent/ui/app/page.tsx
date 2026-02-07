'use client';

import { useLegoAgent } from './hooks/useLegoAgent';
import { TerminalLog } from './components/TerminalLog';
import { InputArea } from './components/InputArea';
import { GraphView } from './components/GraphView';
import { YamlView } from './components/YamlView';
import { GraphAdapter } from './components/GraphAdapter';
import { Terminal, Circle, Layout, FileText, List } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useState } from 'react';

type ViewMode = 'graph' | 'yaml' | 'logs';

export default function Home() {
  const { logs, status, pendingQuestions, sendPrompt, sendAnswers, stopAgent, cwd, updateCwd, dirOptions, listDirs, model, thinkingBudget, graphConfig } = useLegoAgent();
  const [viewMode, setViewMode] = useState<ViewMode>('graph');

  return (
    <main className="flex h-screen flex-col font-mono bg-background text-foreground selection:bg-brand/30">
      <GraphAdapter logs={logs} graphConfig={graphConfig} />
      
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
            {graphConfig && (
                <div className="flex items-center bg-secondary/20 rounded-lg p-0.5 border border-border/50">
                    <button 
                        onClick={() => setViewMode('graph')}
                        className={cn(
                            "flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-medium transition-all",
                            viewMode === 'graph' 
                                ? "bg-background text-blue-400 shadow-sm" 
                                : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                        )}
                    >
                        <Layout className="w-3 h-3" />
                        <span>Graph</span>
                    </button>
                    <button 
                        onClick={() => setViewMode('yaml')}
                        className={cn(
                            "flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-medium transition-all",
                            viewMode === 'yaml' 
                                ? "bg-background text-purple-400 shadow-sm" 
                                : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                        )}
                    >
                        <FileText className="w-3 h-3" />
                        <span>YAML</span>
                    </button>
                    <button 
                        onClick={() => setViewMode('logs')}
                        className={cn(
                            "flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-medium transition-all",
                            viewMode === 'logs' 
                                ? "bg-background text-emerald-400 shadow-sm" 
                                : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                        )}
                    >
                        <List className="w-3 h-3" />
                        <span>Logs</span>
                    </button>
                </div>
            )}
            
            <div className="h-4 w-px bg-border" />
            
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
      <div className="flex-1 flex overflow-hidden relative">
        <div className="flex-1 flex flex-col min-h-0 relative">
            {graphConfig && viewMode === 'graph' && (
                <GraphView className="w-full h-full" />
            )}
            {graphConfig && viewMode === 'yaml' && (
                <YamlView config={graphConfig} className="w-full h-full" />
            )}
            {(!graphConfig || viewMode === 'logs') && (
                <div className="flex-1 w-full max-w-5xl mx-auto flex flex-col min-h-0">
                    <TerminalLog logs={logs} />
                </div>
            )}
        </div>
        
        {/* Sidebar - Terminal Log when Graph or Yaml is active */}
        {graphConfig && viewMode !== 'logs' && (
            <div className="w-96 shrink-0 border-l border-border bg-background/50 overflow-hidden flex flex-col backdrop-blur-sm z-10">
                <div className="p-2 border-b border-border text-xs font-bold text-muted-foreground uppercase bg-secondary/10 flex justify-between items-center">
                    <span>Global Logs</span>
                    <span className="text-[10px] bg-secondary px-1.5 py-0.5 rounded">{logs.length}</span>
                </div>
                <div className="flex-1 overflow-auto">
                     <TerminalLog logs={logs} />
                </div>
            </div>
        )}
      </div>

      {/* Input Area */}
      <div className="shrink-0 bg-background border-t border-border z-20 relative">
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
                onCwdChange={updateCwd}
            />
         </div>
      </div>
    </main>
  );
}
