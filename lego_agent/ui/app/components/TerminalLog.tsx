import React, { useEffect, useRef } from 'react';
import { LogItem } from '../types';
import { cn, stripAnsi } from '@/lib/utils';
import { ChevronRight, Terminal, Activity, Check, X, HelpCircle, Cpu } from 'lucide-react';

interface TerminalLogProps {
  logs: LogItem[];
}

export function TerminalLog({ logs }: TerminalLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  if (logs.length === 0) {
    return (
        <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground opacity-50 space-y-4 font-mono">
            <Terminal className="w-12 h-12" />
            <p className="text-sm">&gt; Awaiting command...</p>
        </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-0 font-mono text-sm selection:bg-white/20 scrollbar-hover">
      {logs.map((item, index) => (
        <LogEntry key={`${item.id}-${index}`} item={item} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

const Header = ({ children, className, timestamp }: { children: React.ReactNode, className?: string, timestamp: string }) => (
    <div className={cn("flex items-start gap-3 opacity-60 mb-1", className)}>
        <span className="text-xs text-muted-foreground select-none shrink-0">[{timestamp}]</span>
        {children}
    </div>
  );

function LogEntry({ item }: { item: LogItem }) {
  const { event } = item;
  const timestamp = new Date(item.timestamp).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });

  if (event.type === 'thinking') {
    if (!event.text) return null;
    return (
        <div className="group animate-in fade-in duration-300">
            <Header timestamp={timestamp}>
                <span className="text-xs uppercase tracking-wider font-bold text-muted-foreground flex items-center gap-2">
                     <Cpu className="w-3 h-3" /> Thinking
                </span>
            </Header>
            <div className="pl-[4.5rem] text-muted-foreground leading-relaxed whitespace-pre-wrap">
                {event.text}
            </div>
        </div>
    );
  }

  if (event.type === 'tool_start') {
    return (
        <div className="group mt-4 mb-2">
            <Header timestamp={timestamp}>
                <span className="text-xs uppercase tracking-wider font-bold text-brand flex items-center gap-2">
                    <Activity className="w-3 h-3" /> Executing
                </span>
            </Header>
            <div className="pl-[4.5rem]">
                 <div className="text-brand font-bold mb-1">
                    &gt; {event.name}
                 </div>
                 <div className="bg-muted/30 border border-border rounded p-2 text-xs overflow-x-auto text-foreground/90">
                    {event.input}
                 </div>
            </div>
        </div>
    );
  }

  if (event.type === 'tool_end') {
    const isError = event.status === 'error';
    return (
      <div className="group mb-4">
         <Header timestamp={timestamp}>
            <span className={cn(
                "text-xs uppercase tracking-wider font-bold flex items-center gap-2",
                isError ? "text-error" : "text-success"
            )}>
                {isError ? <X className="w-3 h-3" /> : <Check className="w-3 h-3" />}
                {isError ? "Failed" : "Completed"}
            </span>
         </Header>
         <div className="pl-[4.5rem]">
            <div className={cn(
                "border-l-2 pl-3 py-1 text-xs overflow-x-auto max-h-80 overflow-y-auto scrollbar-thin scrollbar-thumb-muted-foreground/20",
                isError 
                    ? "border-error/50 text-error/90" 
                    : "border-success/50 text-success"
            )}>
                {event.output}
            </div>
         </div>
      </div>
    );
  }

  if (event.type === 'log') {
     const isError = event.level === 'error';
     const isSuccess = event.level === 'success';
     
     return (
        <div className="group">
             <div className="flex items-start gap-3">
                 <span className="text-xs text-muted-foreground select-none shrink-0 mt-0.5">[{timestamp}]</span>
                 <div className={cn("flex-1 leading-relaxed", 
                     isError ? "text-error" : isSuccess ? "text-success" : "text-foreground"
                 )}>
                     {isError && <span className="font-bold mr-2">ERROR:</span>}
                     {event.message}
                 </div>
             </div>
        </div>
     );
  }

  if (event.type === 'question') {
     return (
        <div className="group my-4 border border-warning/30 bg-warning/5 rounded p-4">
             <div className="flex items-center gap-3 mb-2">
                 <span className="text-xs text-warning select-none">[{timestamp}]</span>
                 <span className="text-xs uppercase tracking-wider font-bold text-warning flex items-center gap-2">
                     <HelpCircle className="w-3 h-3" /> Clarification Needed
                 </span>
             </div>
             <div className="pl-[4.5rem] text-warning font-medium">
                The agent requires input. Please check the command line below.
             </div>
        </div>
     );
  }

  if (event.type === 'script_execution') {
      const displayData = (event.data ?? '')
          .split('\n')
          .filter(line => !line.startsWith('__LEGO_'))
          .join('\n')
          .trim();
      if (!displayData) return null;
      return (
          <div className="pl-[4.5rem] text-xs text-muted-foreground">
              <span className={event.stream === 'stderr' ? 'text-error' : ''}>
                  {stripAnsi(displayData)}
              </span>
          </div>
      );
  }
  
  if (event.type === 'execution_result') {
      return (
          <div className="pl-[4.5rem] mt-1 mb-3">
              <div className="text-xs font-bold text-muted-foreground">
                  &gt; Process exited with code {event.exit_code}
              </div>
          </div>
      );
  }

  return null;
}
