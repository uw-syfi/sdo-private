import React, { useEffect } from 'react';
import ReactFlow, { 
    Background, 
    Controls, 
    Panel 
} from 'reactflow';
import 'reactflow/dist/style.css';
import { useGraphStore } from '../store/graphStore';
import { useElkLayout } from '../hooks/useElkLayout';
import AgentNode from './nodes/AgentNode';
import GroupNode from './nodes/GroupNode';
import { DetailModal } from './DetailModal';

const nodeTypes = {
  agent: AgentNode,
  group: GroupNode,
};

interface GraphViewProps {
    className?: string;
}

export function GraphView({ className }: GraphViewProps) {
  const { 
      nodes, 
      edges, 
      onNodesChange, 
      onEdgesChange, 
      selectNode, 
      selectedNodeId 
  } = useGraphStore();
  
  const { computeLayout } = useElkLayout();
  
  const layoutKey = nodes
    .map((n) => `${n.id}:${n.parentId ?? ''}:${n.style?.width ?? ''}:${n.style?.height ?? ''}`)
    .join('|');

  useEffect(() => {
      if (nodes.length > 0) {
          computeLayout(nodes, edges);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey, edges.length, computeLayout]);

  return (
    <div className={className || "w-full h-full"}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => selectNode(node.id)}
        fitView
        className="bg-slate-950"
      >
        <Background color="#1e293b" gap={20} />
        <Controls className="bg-slate-900 border-slate-800 text-slate-400" />
        
        <Panel position="top-right" className="bg-slate-900/80 p-2 rounded border border-slate-800 text-xs text-slate-400">
            Active: {nodes.filter(n => n.data.status === 'active').length}
        </Panel>
      </ReactFlow>

      <DetailModal 
        isOpen={!!selectedNodeId} 
        agentId={selectedNodeId} 
        onClose={() => selectNode(null)} 
      />
    </div>
  );
}
