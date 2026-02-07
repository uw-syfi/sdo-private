import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { AgentNodeData } from '../../store/graphStore';
import { Loader2, CheckCircle2, XCircle, Clock, Brain } from 'lucide-react';
import { cn } from '@/lib/utils'; 

const StatusIcon = ({ status }: { status: string }) => {
  switch (status) {
    case 'active': return <Loader2 className="w-4 h-4 animate-spin text-emerald-400" />;
    case 'done': return <CheckCircle2 className="w-4 h-4 text-emerald-500" />;
    case 'failed': return <XCircle className="w-4 h-4 text-red-500" />;
    case 'blocked': return <Clock className="w-4 h-4 text-slate-600" />;
    default: return <div className="w-4 h-4 rounded-full border border-slate-700" />;
  }
};

const AgentNode = ({ data, selected }: NodeProps<AgentNodeData>) => {
  return (
    <div className={cn(
      "relative min-w-[180px] bg-slate-900 border transition-all duration-300 shadow-md flex items-center justify-between p-3 rounded-lg",
      selected ? "border-blue-500 ring-1 ring-blue-500" : "border-slate-700",
      data.status === 'active' && "border-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.2)] bg-emerald-950/10",
      data.status === 'blocked' && "opacity-80 border-dashed bg-slate-900/50",
      data.status === 'done' && "border-slate-700 bg-slate-900"
    )}>
      <Handle type="target" position={Position.Top} className="!bg-slate-600 !w-2 !h-2" />
      <Handle type="source" position={Position.Top} id="source-top" className="!bg-slate-600 !w-2 !h-2" style={{ left: '70%' }} />
      
      <div className="flex items-center gap-3 w-full">
        <div className={cn(
          "p-1.5 rounded-md flex-shrink-0",
          data.status === 'active' ? "bg-emerald-500/10" : "bg-slate-800"
        )}>
           <Brain className={cn(
             "w-4 h-4",
             data.status === 'active' ? "text-emerald-400" : "text-slate-400"
           )} />
        </div>
        
        <div className="flex-1 min-w-0">
          <div className="font-medium text-slate-200 text-sm truncate">{data.label}</div>
          <div className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold truncate">
            {data.pattern || 'worker'}
          </div>
        </div>

        <StatusIcon status={data.status} />
      </div>

      <Handle type="target" position={Position.Bottom} id="target-bottom" className="!bg-slate-600 !w-2 !h-2" style={{ left: '30%' }} />
      <Handle type="source" position={Position.Bottom} className="!bg-slate-600 !w-2 !h-2" />
    </div>
  );
};

export default memo(AgentNode);
