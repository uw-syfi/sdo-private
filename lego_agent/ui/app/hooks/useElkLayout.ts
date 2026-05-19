import { useCallback, useRef } from 'react';
import ELK from 'elkjs/lib/elk.bundled';
import { useGraphStore } from '../store/graphStore';
import { Node, Edge } from 'reactflow';
import {
  computeGroupContentBounds,
  computeWorkerGridLayout,
  DEFAULT_CELL_HEIGHT,
  DEFAULT_CELL_WIDTH,
  DEFAULT_PADDING,
} from '../utils/workerGridLayout';

const elk = new ELK();

const elkPadding = `[top=${DEFAULT_PADDING},left=${DEFAULT_PADDING},bottom=${DEFAULT_PADDING},right=${DEFAULT_PADDING}]`;

// Layout options for ELK — horizontal flow between pipeline stages
const layoutOptions = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.spacing.nodeNode': '60',
  'elk.layered.spacing.nodeNodeBetweenLayers': '80',
  'elk.padding': elkPadding,
};

export const useElkLayout = () => {
  const { setNodes } = useGraphStore();
  const layoutVersionRef = useRef(0);

  const computeLayout = useCallback(async (nodes: Node[], edges: Edge[]) => {
    const myVersion = ++layoutVersionRef.current;
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

      // Abort if a newer layout was triggered while ELK was running.
      if (myVersion !== layoutVersionRef.current) return;

      // 3. Collect ELK-assigned positions into a map (keyed by node id).
      //    We do NOT replace the store with the snapshot — we merge positions
      //    onto the *current* store so that nodes added after the snapshot was
      //    taken (e.g. fan_out workers) are never accidentally dropped.
      const positionMap = new Map<string, { x: number; y: number; width: number; height: number }>();

      const collectPositions = (elkNode: any) => {
        positionMap.set(elkNode.id, {
          x: elkNode.x ?? 0,
          y: elkNode.y ?? 0,
          width: elkNode.width,
          height: elkNode.height,
        });
        if (elkNode.children) {
          elkNode.children.forEach((child: any) => collectPositions(child));
        }
      };

      if (layoutedGraph.children) {
        layoutedGraph.children.forEach((child: any) => collectPositions(child));
      }

      // Fan-out workers have no edges between siblings; ELK stacks them at (0,0).
      // Override with an explicit grid inside each fan_out group.
      const liveNodes = useGraphStore.getState().nodes;
      for (const groupNode of liveNodes) {
        if (groupNode.type !== 'group' || groupNode.data?.pattern !== 'fan_out') {
          continue;
        }
        const children = liveNodes.filter(
          (n) => n.parentId === groupNode.id && n.type === 'agent',
        );
        if (children.length === 0) continue;

        const { positions, bounds } = computeWorkerGridLayout(children.length);
        const groupLayout = positionMap.get(groupNode.id);

        children.forEach((child, i) => {
          const existing = positionMap.get(child.id);
          positionMap.set(child.id, {
            x: positions[i].x,
            y: positions[i].y,
            width: existing?.width ?? DEFAULT_CELL_WIDTH,
            height: existing?.height ?? DEFAULT_CELL_HEIGHT,
          });
        });

        positionMap.set(groupNode.id, {
          x: groupLayout?.x ?? groupNode.position.x,
          y: groupLayout?.y ?? groupNode.position.y,
          width: bounds.width,
          height: bounds.height,
        });
      }

      // Resize every group (deepest first) so nested boxes fit their children + padding.
      const groupDepth = (nodeId: string): number => {
        let depth = 0;
        let parentId = liveNodes.find((n) => n.id === nodeId)?.parentId;
        while (parentId) {
          depth += 1;
          parentId = liveNodes.find((n) => n.id === parentId)?.parentId;
        }
        return depth;
      };

      const groupNodes = liveNodes
        .filter((n) => n.type === 'group')
        .sort((a, b) => groupDepth(b.id) - groupDepth(a.id));

      for (const groupNode of groupNodes) {
        const children = liveNodes.filter((n) => n.parentId === groupNode.id);
        if (children.length === 0) continue;

        const childRects = children.map((child) => {
          const layout = positionMap.get(child.id);
          return {
            x: layout?.x ?? child.position.x,
            y: layout?.y ?? child.position.y,
            width: layout?.width ?? Number(child.style?.width) ?? DEFAULT_CELL_WIDTH,
            height: layout?.height ?? Number(child.style?.height) ?? DEFAULT_CELL_HEIGHT,
          };
        });

        const { width, height, topInset } = computeGroupContentBounds(childRects);
        if (topInset > 0) {
          children.forEach((child) => {
            const layout = positionMap.get(child.id);
            if (!layout) return;
            positionMap.set(child.id, { ...layout, y: layout.y + topInset });
          });
        }

        const groupLayout = positionMap.get(groupNode.id);
        const prevWidth = groupLayout?.width ?? Number(groupNode.style?.width) ?? 0;
        const prevHeight = groupLayout?.height ?? Number(groupNode.style?.height) ?? 0;
        positionMap.set(groupNode.id, {
          x: groupLayout?.x ?? groupNode.position.x,
          y: groupLayout?.y ?? groupNode.position.y,
          width: Math.max(prevWidth, width),
          height: Math.max(prevHeight, height),
        });
      }

      // 4. Apply positions to the *live* store state (not the stale snapshot).
      //    This guarantees the merged array is always structurally valid for
      //    ReactFlow (no orphaned children with missing parents).
      const currentNodes = useGraphStore.getState().nodes;
      const merged = currentNodes.map((n) => {
        const layout = positionMap.get(n.id);
        if (!layout) return n;
        return {
          ...n,
          position: { x: layout.x, y: layout.y },
          style: {
            ...n.style,
            ...(layout.width != null ? { width: layout.width } : {}),
            ...(layout.height != null ? { height: layout.height } : {}),
          },
        };
      });

      // Check if any positioned node actually moved to prevent infinite loops.
      const hasChanges = merged.some((newNode) => {
          const oldNode = currentNodes.find((n) => n.id === newNode.id);
          if (!oldNode) return true;
          const posChanged = Math.abs(newNode.position.x - oldNode.position.x) > 1 ||
                             Math.abs(newNode.position.y - oldNode.position.y) > 1;
          const sizeChanged = Math.abs(Number(newNode.style?.width) - Number(oldNode.style?.width)) > 1 ||
                              Math.abs(Number(newNode.style?.height) - Number(oldNode.style?.height)) > 1;
          return posChanged || sizeChanged;
      });

      if (hasChanges) {
          setNodes(merged);
      }
      
    } catch (err) {
      console.error('ELK Layout failed:', err);
    }
  }, [setNodes]);

  return { computeLayout };
};
