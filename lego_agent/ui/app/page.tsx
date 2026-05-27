'use client';

import { useLegoAgent } from './hooks/useLegoAgent';
import { useDemoSimulation } from './hooks/useDemoSimulation';
import { TerminalLog } from './components/TerminalLog';
import { InputArea } from './components/InputArea';
import { GraphView } from './components/GraphView';
import { YamlView } from './components/YamlView';
import { GraphAdapter } from './components/GraphAdapter';
import { DemoGallery } from './components/DemoGallery';
import { QueueDebugPanel } from './components/QueueDebugPanel';
import { Terminal, Circle, Layout, FileText, List, Play } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useState } from 'react';

type ViewMode = 'graph' | 'yaml' | 'logs';

export default function Home() {
    const { logs: realLogs, status, pendingQuestions, sendPrompt, sendYaml, sendAnswers, stopAgent, cwd, updateCwd, dirOptions, listDirs, model, thinkingBudget, graphConfig: realGraphConfig, queueSnapshot } = useLegoAgent();
  const [viewMode, setViewMode] = useState<ViewMode>('graph');
  
  // Demo Mode State
  const [demoMode, setDemoMode] = useState<string | null>(null); // null = off, 'selecting' = gallery, 'pattern_id' = running
  const isDemo = demoMode !== null;
  const isDemoRunning = isDemo && demoMode !== 'selecting';
  
  const { logs: demoLogs, graphConfig: demoGraphConfig } = useDemoSimulation(isDemoRunning ? demoMode : null);
  
  // Effective data
  const logs = isDemoRunning ? demoLogs : realLogs;
  const graphConfig = isDemoRunning ? demoGraphConfig : realGraphConfig;

  return (
    <main className="flex h-screen flex-col font-mono bg-background text-foreground selection:bg-brand/30">
      {/* Run GraphAdapter only when we have a config (real or demo) */}
      <GraphAdapter logs={logs} graphConfig={graphConfig} />
      
      {/* Header */}
      <header className="h-12 border-b border-border flex items-center px-4 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 shrink-0 z-10 sticky top-0 justify-between">
        <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 text-foreground/90 hover:text-foreground transition-colors">
                <Terminal className="w-4 h-4" />
                <span className="font-bold text-sm tracking-tight">LegoAgent</span>
            </div>
            
            <div className="h-4 w-px bg-border mx-1" />
            
            <button
                onClick={() => setDemoMode(isDemo ? null : 'selecting')}
                className={cn(
                    "flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-medium transition-all border border-border/50",
                    isDemo 
                        ? "bg-purple-500/10 text-purple-400 border-purple-500/20" 
                        : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                )}
            >
                <Play className="w-3 h-3" />
                <span>{isDemo ? 'Exit Demo' : 'Demo Mode'}</span>
            </button>
            
            {isDemo && demoMode !== 'selecting' && (
                 <button
                    onClick={() => setDemoMode('selecting')}
                    className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-medium transition-all border border-border/50 text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                >
                    <List className="w-3 h-3" />
                    <span>Back to Gallery</span>
                </button>
            )}

            {!isDemo && (status === 'disconnected' || status === 'connecting') && (
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
            
            {model && !isDemo && (
                <div className="flex items-center gap-1.5">
                    <span className="text-muted-foreground/60">model:</span>
                    <span className="font-bold text-foreground">{model}</span>
                </div>
            )}
            {thinkingBudget && !isDemo && (
                 <div className="flex items-center gap-1.5">
                    <span className="text-muted-foreground/60">thinking:</span>
                    <span className="font-bold text-foreground">{thinkingBudget} tok</span>
                </div>
            )}
            {isDemo && (
                 <div className="flex items-center gap-1.5">
                    <span className="text-purple-400 font-bold">DEMO MODE</span>
                </div>
            )}
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden relative">
        <div className="flex-1 flex flex-col min-h-0 relative">
            {demoMode === 'selecting' ? (
                <DemoGallery onSelect={setDemoMode} />
            ) : (
                <>
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
                </>
            )}
        </div>
        
        {/* Sidebar - Terminal Log when Graph or Yaml is active */}
        {graphConfig && viewMode !== 'logs' && demoMode !== 'selecting' && (
            <div className="w-96 shrink-0 border-l border-border bg-background/50 overflow-hidden flex flex-col backdrop-blur-sm z-10">
                <div className="p-2 border-b border-border text-xs font-bold text-muted-foreground uppercase bg-secondary/10 flex justify-between items-center">
                    <span>Queue Debug</span>
                    <span className="text-[10px] bg-secondary px-1.5 py-0.5 rounded">{queueSnapshot?.stages.length ?? 0} stages</span>
                </div>
                <div className="p-3 border-b border-border/70 bg-background/70 overflow-y-auto max-h-[42vh]">
                    <QueueDebugPanel snapshot={queueSnapshot} />
                </div>
                <div className="p-2 border-b border-border text-xs font-bold text-muted-foreground uppercase bg-secondary/10 flex justify-between items-center">
                    <span>Global Logs</span>
                    <span className="text-[10px] bg-secondary px-1.5 py-0.5 rounded">{logs.length}</span>
                </div>
                <div className="flex-1 flex flex-col min-h-0">
                     <TerminalLog logs={logs} />
                </div>
            </div>
        )}
      </div>

      {/* Input Area - Hide in demo mode or make read-only */}
      <div className="shrink-0 bg-background border-t border-border z-20 relative">
         <div className="w-full max-w-5xl mx-auto">
            {!isDemo ? (
                <InputArea
                    onSendPrompt={sendPrompt}
                    onSendYaml={sendYaml}
                    onSendAnswers={sendAnswers}
                    onStop={stopAgent} 
                    pendingQuestions={pendingQuestions}
                    status={status}
                    initialCwd={cwd}
                    dirOptions={dirOptions}
                    onListDirs={listDirs}
                    onCwdChange={updateCwd}
                />
            ) : (
                <div className="p-4 text-center text-xs text-muted-foreground flex items-center justify-center gap-2">
                    <Play className="w-3 h-3 text-purple-400 animate-pulse" />
                    Running Simulation: <span className="text-foreground font-medium">{demoMode === 'selecting' ? 'Select a pattern' : demoMode}</span>
                </div>
            )}
         </div>
      </div>
    </main>
  );
}
