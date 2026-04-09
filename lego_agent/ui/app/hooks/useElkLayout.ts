import { useCallback } from 'react';
import ELK from 'elkjs/lib/elk.bundled';
import { useGraphStore } from '../store/graphStore';
import { Node, Edge } from 'reactflow';

const elk = new ELK();

// Layout options for ELK
const layoutOptions = {
  'elk.algorithm': 'layered',
  'elk.direction': 'DOWN',
  'elk.spacing.nodeNode': '60',
  'elk.layered.spacing.nodeNodeBetweenLayers': '80',
  'elk.padding': '[top=30,left=30,bottom=30,right=30]',
};

export const useElkLayout = () => {
  const { setNodes } = useGraphStore();

  const computeLayout = useCallback(async (nodes: Node[], edges: Edge[]) => {
    if (nodes.length === 0) return;

    // 1. Build hierarchy tree from flat ReactFlow nodes
    const graph: any = {
      id: 'root',
      layoutOptions,
      children: [],
      edges: [],
    };

    const nodeMap = new Map();
    const parentMap = new Map();
    
    // Initialize ELK nodes and parent map
    nodes.forEach((node) => {
      if (node.parentId) {
          parentMap.set(node.id, node.parentId);
      }
      
      const isJudgeLoop = node.data?.pattern === 'judge_loop';
      const elkNode = {
        id: node.id,
        width: node.width ?? (typeof node.style?.width === 'number' ? node.style.width : 200), 
        height: node.height ?? (typeof node.style?.height === 'number' ? node.style.height : 64),
        layoutOptions: {
            ...(isJudgeLoop ? { 
                'elk.direction': 'RIGHT',
                'elk.spacing.nodeNode': '80'
            } : {})
        },
        children: [],
        edges: [],
      };
      nodeMap.set(node.id, elkNode);
    });

    // Build tree
    nodes.forEach((node) => {
      const elkNode = nodeMap.get(node.id);
      if (node.parentId) {
        const parent = nodeMap.get(node.parentId);
        if (parent) {
          parent.children.push(elkNode);
        } else {
            graph.children.push(elkNode);
        }
      } else {
        graph.children.push(elkNode);
      }
    });

    // Helper to find LCA
    const getAncestors = (id: string) => {
        const ancestors = [];
        let current = id;
        while (current) {
            ancestors.unshift(current);
            current = parentMap.get(current);
        }
        return ancestors;
    };

    // Distribute edges to LCA
    edges.forEach((edge) => {
        const sourceAncestors = getAncestors(edge.source);
        const targetAncestors = getAncestors(edge.target);
        
        // Find LCA
        let lcaId = 'root';
        const minLen = Math.min(sourceAncestors.length, targetAncestors.length);
        
        for (let i = 0; i < minLen; i++) {
            if (sourceAncestors[i] === targetAncestors[i]) {
                lcaId = sourceAncestors[i];
            } else {
                break;
            }
        }
        
        const elkEdge = { 
            id: edge.id, 
            sources: [edge.source], 
            targets: [edge.target] 
        };

        if (lcaId === 'root') {
            graph.edges.push(elkEdge);
        } else {
            const lcaNode = nodeMap.get(lcaId);
            if (lcaNode) {
                lcaNode.edges.push(elkEdge);
            } else {
                // Fallback to root if LCA not found (shouldn't happen)
                graph.edges.push(elkEdge);
            }
        }
    });

    try {
      // 2. Compute Layout
      const layoutedGraph = await elk.layout(graph);

      // 3. Flatten back to ReactFlow nodes
      const nextNodes: Node[] = [];
      
      const processNode = (elkNode: any) => {
        // Find original node to preserve data
        const originalNode = nodes.find((n) => n.id === elkNode.id);
        if (originalNode) {
          nextNodes.push({
            ...originalNode,
            position: {
              x: elkNode.x,
              y: elkNode.y,
            },
            style: {
                ...originalNode.style,
                width: elkNode.width,
                height: elkNode.height,
            }
          });
        }
        
        // Recurse
        if (elkNode.children) {
            elkNode.children.forEach((child: any) => processNode(child));
        }
      };

      if (layoutedGraph.children) {
          layoutedGraph.children.forEach((child: any) => processNode(child));
      }

      // Check if nodes actually changed to prevent infinite loops
      const hasChanges = nextNodes.some((newNode) => {
          const oldNode = nodes.find((n) => n.id === newNode.id);
          if (!oldNode) return true;
          
          const posChanged = Math.abs(newNode.position.x - oldNode.position.x) > 1 || 
                             Math.abs(newNode.position.y - oldNode.position.y) > 1;
          
          const sizeChanged = Math.abs(Number(newNode.style?.width) - Number(oldNode.style?.width)) > 1 ||
                              Math.abs(Number(newNode.style?.height) - Number(oldNode.style?.height)) > 1;
                              
          return posChanged || sizeChanged;
      });

      if (hasChanges) {
          setNodes(nextNodes);
      }
      
    } catch (err) {
      console.error('ELK Layout failed:', err);
    }
  }, [setNodes]);

  return { computeLayout };
};
