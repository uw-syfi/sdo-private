import React from 'react';
import { Network, GitFork, List, MessageSquare, CheckCircle, Brain, Play } from 'lucide-react';
import { cn } from '@/lib/utils';

interface GraphViewProps {
  config: any;
  className?: string;
}

const NodeIcon = ({ type }: { type: string }) => {
  switch (type) {
    case 'agent': return <Brain className="w-4 h-4 text-blue-400" />;
    case 'chain': return <List className="w-4 h-4 text-purple-400" />;
    case 'fan_out': return <GitFork className="w-4 h-4 text-orange-400" />;
    case 'summarize': return <MessageSquare className="w-4 h-4 text-green-400" />;
    case 'judge_loop': return <CheckCircle className="w-4 h-4 text-red-400" />;
    default: return <Play className="w-4 h-4 text-gray-400" />;
  }
};

const Node = ({ data, label, depth = 0 }: { data: any, label?: string, depth?: number }) => {
  if (!data || typeof data !== 'object') return null;

  const type = data.type || 'unknown';
  const instruction = data.instruction || data.task || (data.items ? `${data.items.length} items` : '');
  
  return (
    <div className={cn("flex flex-col gap-2", depth > 0 && "ml-4 border-l border-border pl-4")}>
      <div className="flex items-start gap-3 p-2 rounded-md bg-secondary/30 hover:bg-secondary/50 transition-colors border border-border/50">
        <div className="mt-0.5"><NodeIcon type={type} /></div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">{type.replace('_', ' ')}</span>
            {label && <span className="text-xs font-mono text-blue-300 bg-blue-950/30 px-1.5 py-0.5 rounded">{label}</span>}
          </div>
          {instruction && (
            <p className="text-sm text-foreground/80 mt-1 line-clamp-2 text-ellipsis overflow-hidden">
              {typeof instruction === 'string' ? instruction : JSON.stringify(instruction)}
            </p>
          )}
          {data.model && (
             <p className="text-xs text-muted-foreground mt-1">Model: {data.model}</p>
          )}
        </div>
      </div>

      {/* Children */}
      <div className="flex flex-col gap-2">
        {data.steps && data.steps.map((step: any, i: number) => (
          <Node key={i} data={step} label={`Step ${i + 1}`} depth={depth + 1} />
        ))}
        
        {data.agent && (
          <Node data={data.agent} label="Agent" depth={depth + 1} />
        )}

        {data.worker && (
          <Node data={data.worker} label="Worker" depth={depth + 1} />
        )}

        {data.judge && (
          <Node data={data.judge} label="Judge" depth={depth + 1} />
        )}
      </div>
    </div>
  );
};

export function GraphView({ config, className }: GraphViewProps) {
  if (!config || !config.workflow) return null;

  return (
    <div className={cn("flex flex-col h-full bg-background border-l border-border", className)}>
      <div className="flex items-center gap-2 p-4 border-b border-border bg-secondary/10">
        <Network className="w-4 h-4" />
        <h3 className="font-semibold text-sm">Execution Plan</h3>
      </div>
      <div className="flex-1 overflow-auto p-4">
        <Node data={config.workflow} label="Root" />
      </div>
    </div>
  );
}
