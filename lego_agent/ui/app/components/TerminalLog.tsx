import React, { useEffect, useRef } from 'react';
import { LogItem } from '../types';
import { cn } from '@/lib/utils';
import { Terminal, Cpu, PenTool, CheckCircle, XCircle, Info } from 'lucide-react';

interface TerminalLogProps {
  logs: LogItem[];
}

export function TerminalLog({ logs }: TerminalLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  return (
    <div className="flex-1 overflow-y-auto bg-black p-4 font-mono text-sm text-gray-300 space-y-2">
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
    return <div className="text-gray-500 italic whitespace-pre-wrap">{event.text}</div>;
  }

  if (event.type === 'tool_start') {
    return (
      <div className="border border-blue-900 bg-blue-950/20 p-2 rounded my-2">
        <div className="flex items-center gap-2 text-blue-400 font-bold">
            <PenTool size={16} />
            {event.name}
        </div>
        <div className="text-gray-400 mt-1 whitespace-pre-wrap">{event.input}</div>
      </div>
    );
  }

  if (event.type === 'tool_end') {
    const isError = event.status === 'error';
    return (
      <div className={cn("border p-2 rounded my-2", isError ? "border-red-900 bg-red-950/20" : "border-green-900 bg-green-950/20")}>
         <div className={cn("flex items-center gap-2 font-bold", isError ? "text-red-400" : "text-green-400")}>
            {isError ? <XCircle size={16} /> : <CheckCircle size={16} />}
            Result ({event.status})
        </div>
        <div className="text-gray-400 mt-1 whitespace-pre-wrap max-h-60 overflow-y-auto">{event.output}</div>
      </div>
    );
  }

  if (event.type === 'log') {
     const color = event.level === 'error' ? 'text-red-500' : event.level === 'success' ? 'text-green-500' : 'text-blue-400';
     return (
        <div className="flex gap-2">
            <span className={color}>
                {event.level === 'error' ? '!' : 'ℹ'}
            </span>
            <span>{event.message}</span>
        </div>
     );
  }

  if (event.type === 'question') {
     return (
        <div className="text-yellow-400 font-bold my-2">
            ? Agent asked for clarification...
        </div>
     );
  }

  if (event.type === 'script_execution') {
      return (
          <div className="flex gap-2">
              <span className="text-purple-500 select-none">│</span>
              <span className={event.stream === 'stderr' ? 'text-red-400' : 'text-gray-300'}>
                  {event.data}
              </span>
          </div>
      );
  }
  
  if (event.type === 'execution_result') {
      return (
          <div className="border-t border-gray-800 pt-2 mt-2">
              Exit Code: {event.exit_code}
          </div>
      );
  }

  return null;
}
