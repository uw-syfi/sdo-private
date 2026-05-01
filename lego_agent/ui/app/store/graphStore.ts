import { create } from 'zustand';
import { Node, Edge, NodeChange, EdgeChange, applyNodeChanges, applyEdgeChanges } from 'reactflow';
import { LogItem } from '../types';

export type AgentStatus = 'pending' | 'active' | 'blocked' | 'done' | 'failed';

export type AgentNodeData = {
  label: string;
  status: AgentStatus;
  pattern?: 'fan_out' | 'judge_loop' | 'summarize' | 'worker' | 'chain' | 'group' | string;
  currentThought?: string;
  logs: LogItem[];
};

export interface GraphState {
  nodes: Node<AgentNodeData>[];
  edges: Edge[];
  selectedNodeId: string | null;

  setNodes: (nodes: Node<AgentNodeData>[]) => void;
  setEdges: (edges: Edge[]) => void;
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  selectNode: (id: string | null) => void;
  addNodes: (nodes: Node<AgentNodeData>[]) => void;
  removeNodes: (ids: string[]) => void;
  updateNodeStatus: (id: string, status: AgentStatus) => void;
  updateNodeThought: (id: string, thought: string) => void;
  addNodeLog: (id: string, log: LogItem) => void;
  updateEdge: (id: string, update: Partial<Edge>) => void;
}

export const useGraphStore = create<GraphState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,

  setNodes: (nodes) => set({ nodes }),
  setEdges: (edges) => set({ edges }),

  onNodesChange: (changes) => {
    set({
      nodes: applyNodeChanges(changes, get().nodes) as Node<AgentNodeData>[],
    });
  },
  onEdgesChange: (changes) => {
    set({
      edges: applyEdgeChanges(changes, get().edges),
    });
  },

  selectNode: (id) => set({ selectedNodeId: id }),

  addNodes: (nodes) => set((state) => ({ nodes: [...state.nodes, ...nodes] })),
  removeNodes: (ids) => set((state) => ({ nodes: state.nodes.filter((n) => !ids.includes(n.id)) })),

  updateNodeStatus: (id, status) => set((state) => ({
    nodes: state.nodes.map((node) =>
      node.id === id
        ? { ...node, data: { ...node.data, status } }
        : node
    ),
  })),

  updateNodeThought: (id, thought) => set((state) => ({
    nodes: state.nodes.map((node) =>
      node.id === id
        ? { ...node, data: { ...node.data, currentThought: thought } }
        : node
    ),
  })),

  updateEdge: (id, update) => set((state) => ({
    edges: state.edges.map(edge => edge.id === id ? { ...edge, ...update } : edge),
  })),

  addNodeLog: (id, log) => set((state) => ({
    nodes: state.nodes.map((node) => {
      if (node.id !== id) return node;
      const existing = node.data.logs || [];
      // Upsert: update the entry if this log ID was already added (happens when
      // script_execution events are coalesced — same ID, growing data field).
      const idx = existing.findIndex(l => l.id === log.id);
      const logs = idx >= 0
        ? existing.map((l, i) => i === idx ? log : l)
        : [...existing, log];
      return { ...node, data: { ...node.data, logs } };
    }),
  })),
}));
