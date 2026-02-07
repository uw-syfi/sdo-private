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
      edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
    };

    const nodeMap = new Map();
    
    // Initialize ELK nodes
    nodes.forEach((node) => {
      const elkNode = {
        id: node.id,
        width: node.width || 200, // Compact width
        height: node.height || 64, // Compact height
        layoutOptions: {
            // 'elk.padding': '[top=20,left=20,bottom=20,right=20]'
        },
        children: [],
        edges: [],
      };
      nodeMap.set(node.id, elkNode);
    });

    // Build tree
    nodes.forEach((node) => {
      const elkNode = nodeMap.get(node.id);
      if (node.parentNode) {
        const parent = nodeMap.get(node.parentNode);
        if (parent) {
          parent.children.push(elkNode);
        } else {
            // Parent not found, fallback to root
            graph.children.push(elkNode);
        }
      } else {
        graph.children.push(elkNode);
      }
    });

    try {
      // 2. Compute Layout
      const layoutedGraph = await elk.layout(graph);

      // 3. Flatten back to ReactFlow nodes
      const nextNodes: Node[] = [];
      
      const processNode = (elkNode: any, parentX = 0, parentY = 0) => {
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
            elkNode.children.forEach((child: any) => processNode(child, elkNode.x, elkNode.y));
        }
      };

      if (layoutedGraph.children) {
          layoutedGraph.children.forEach((child: any) => processNode(child));
      }

      setNodes(nextNodes);
      
    } catch (err) {
      console.error('ELK Layout failed:', err);
    }
  }, [setNodes]);

  return { computeLayout };
};
