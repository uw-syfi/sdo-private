import { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { AgentNodeData } from '../../store/graphStore';
import { cn } from '@/lib/utils';

const GroupNode = ({ data, selected }: NodeProps<AgentNodeData>) => {
  return (
    <div className={cn(
      "relative w-full h-full min-w-[200px] min-h-[100px] rounded-xl border-2 transition-all duration-300",
      data.status === 'active' ? 'border-emerald-500/30 bg-emerald-900/5' : 'border-slate-800 bg-slate-900/20',
      selected && 'ring-2 ring-blue-500'
    )}>
      <Handle type="target" position={Position.Left} id="target-left" className="!w-0 !h-0 opacity-0 pointer-events-none border-0" isConnectable={false} />
      <Handle type="source" position={Position.Right} id="source-right" className="!w-0 !h-0 opacity-0 pointer-events-none border-0" isConnectable={false} />
      <div className="absolute -top-3 left-4 px-2 bg-slate-950 border border-slate-800 rounded text-[10px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-2">
        <span>{data.label}</span>
        {data.status === 'active' && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />}
      </div>
    </div>
  );
};

export default memo(GroupNode);
