import React, { useEffect } from 'react';
import ReactFlow, { 
    Background, 
    Controls, 
    Panel, 
    MiniMap 
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
  
  useEffect(() => {
      // Debounce or check if layout needed? 
      // For now run on structure change
      if (nodes.length > 0) {
          computeLayout(nodes, edges);
      }
  }, [nodes.length, edges.length, computeLayout]);

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
        <MiniMap 
            nodeColor={(n) => n.type === 'group' ? '#1e293b' : '#3b82f6'} 
            className="bg-slate-900 border-slate-800"
        />
        
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
