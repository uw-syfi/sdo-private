import React, { useEffect, useRef } from 'react';
import { LogItem } from '../types';
import { cn } from '@/lib/utils';
import { PenTool, CheckCircle2, AlertCircle, Cpu, ArrowRight } from 'lucide-react';

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
        <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground opacity-50 space-y-4">
            <Cpu className="w-12 h-12" />
            <p className="text-sm">Ready to build something amazing.</p>
        </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-6">
      {logs.map((item) => (
        <LogEntry key={item.id} item={item} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

function LogEntry({ item }: { item: LogItem }) {
  const { event } = item;

  if (event.type === 'thinking') {
    // Accumulate thinking text. If it's just a small chunk, it might look jumpy, but usually it comes in streams.
    // We'll style it as a subtle "thought" block.
    if (!event.text) return null;
    return (
        <div className="flex gap-4 group">
            <div className="w-8 flex flex-col items-center pt-1">
                 <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center">
                    <Cpu className="w-4 h-4 text-muted-foreground" />
                 </div>
                 <div className="w-px h-full bg-border my-2 group-last:hidden" />
            </div>
            <div className="flex-1 pb-6">
                <div className="text-sm text-muted-foreground italic leading-relaxed whitespace-pre-wrap font-mono">
                    {event.text}
                </div>
            </div>
        </div>
    );
  }

  if (event.type === 'tool_start') {
    return (
        <div className="flex gap-4 group">
             <div className="w-8 flex flex-col items-center pt-1">
                 <div className="w-8 h-8 rounded-full bg-brand-blue/10 flex items-center justify-center border border-brand-blue/20">
                    <PenTool className="w-4 h-4 text-brand-blue" />
                 </div>
                 <div className="w-px h-full bg-border my-2 group-last:hidden" />
            </div>
            <div className="flex-1 pb-6">
                 <div className="text-xs font-semibold text-brand-blue mb-1.5 flex items-center gap-2 font-mono">
                    Running Tool <ArrowRight className="w-3 h-3" /> {event.name}
                 </div>
                 <div className="bg-muted/50 rounded-md border border-border p-3 font-mono text-xs overflow-x-auto text-foreground/80">
                    {event.input}
                 </div>
            </div>
        </div>
    );
  }

  if (event.type === 'tool_end') {
    const isError = event.status === 'error';
    return (
      <div className="flex gap-4 group">
         <div className="w-8 flex flex-col items-center pt-1">
             <div className={cn(
                 "w-8 h-8 rounded-full flex items-center justify-center border",
                 isError 
                    ? "bg-red-100 dark:bg-red-900/30 border-red-200 dark:border-red-800" 
                    : "bg-emerald-100 dark:bg-emerald-900/30 border-emerald-200 dark:border-emerald-800"
             )}>
                {isError 
                    ? <AlertCircle className="w-4 h-4 text-red-600 dark:text-red-400" /> 
                    : <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                }
             </div>
             <div className="w-px h-full bg-border my-2 group-last:hidden" />
         </div>
         <div className="flex-1 pb-6">
            <div className={cn("text-xs font-semibold mb-1.5", isError ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400")}>
                {isError ? "Tool Failed" : "Tool Completed"}
            </div>
            <div className={cn(
                "rounded-md border p-3 font-mono text-xs overflow-x-auto max-h-80 overflow-y-auto scrollbar-thin scrollbar-thumb-muted-foreground/20",
                isError 
                    ? "bg-red-50/50 dark:bg-red-950/10 border-red-200 dark:border-red-900 text-red-700 dark:text-red-300" 
                    : "bg-emerald-50/50 dark:bg-emerald-950/10 border-emerald-200 dark:border-emerald-900 text-emerald-700 dark:text-emerald-300"
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
        <div className="flex gap-4 group">
             <div className="w-8 flex flex-col items-center pt-1">
                 <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 mt-2" />
                 <div className="w-px h-full bg-border my-2 group-last:hidden" />
             </div>
             <div className="flex-1 pb-4 pt-1">
                <div className={cn("text-sm", 
                    isError ? "text-destructive" : isSuccess ? "text-emerald-600" : "text-muted-foreground"
                )}>
                    {event.message}
                </div>
             </div>
        </div>
     );
  }

  if (event.type === 'question') {
     return (
        <div className="flex gap-4 group">
             <div className="w-8 flex flex-col items-center pt-1">
                 <div className="w-8 h-8 rounded-full bg-amber-100 dark:bg-amber-900/30 flex items-center justify-center border border-amber-200 dark:border-amber-800">
                    <span className="text-amber-600 dark:text-amber-400 font-bold text-lg">?</span>
                 </div>
             </div>
             <div className="flex-1 py-2">
                <div className="text-amber-600 dark:text-amber-400 font-medium">
                    The agent requires clarification. Please check the input area.
                </div>
             </div>
        </div>
     );
  }

  if (event.type === 'script_execution') {
      return (
          <div className="flex gap-4 group pl-4 border-l-2 border-primary/20 ml-3.5 my-1">
              <div className="flex-1 font-mono text-xs text-muted-foreground">
                  <span className={event.stream === 'stderr' ? 'text-destructive' : ''}>
                      {event.data}
                  </span>
              </div>
          </div>
      );
  }
  
  if (event.type === 'execution_result') {
      return (
          <div className="flex gap-4 group pl-4 ml-3.5 my-2">
              <div className="flex-1 text-xs font-medium text-muted-foreground border-t border-border pt-2">
                  Process exited with code {event.exit_code}
              </div>
          </div>
      );
  }

  return null;
}
