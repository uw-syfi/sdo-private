import { useEffect, useRef } from 'react';
import { useGraphStore, AgentNodeData } from '../store/graphStore';
import { LogItem } from '../types';
import { Node, Edge, MarkerType } from 'reactflow';

interface GraphConfigNode {
  id?: string;
  type?: string;
  label?: string;
  name?: string;
  instruction?: string;
  task?: string;
  steps?: GraphConfigNode[];
  worker?: GraphConfigNode;
  judge?: GraphConfigNode;
  [key: string]: any;
}

interface GraphConfig {
  workflow: GraphConfigNode;
}

interface GraphAdapterProps {
  logs: LogItem[];
  graphConfig: GraphConfig;
}

/**
 * Split a coalesced script_execution data string into per-step segments.
 * Each segment spans from one __LEGO_STEP_START__ marker to the next (or end).
 * Returns a map of stepIndex → content string.
 */
function extractStepSegments(data: string): Map<number, string> {
  const segments = new Map<number, string>();
  const matches = [...data.matchAll(/(?:^|\n)__LEGO_STEP_START__ (\d+)/g)];

  for (let i = 0; i < matches.length; i++) {
    const step = parseInt(matches[i][1], 10);
    const start = matches[i].index!;
    const end = i + 1 < matches.length ? matches[i + 1].index! : data.length;
    segments.set(step, data.slice(start, end));
  }

  return segments;
}

export function GraphAdapter({ logs, graphConfig }: GraphAdapterProps) {
  const { setNodes, setEdges, addNodes, removeNodes, updateNodeStatus, updateNodeThought, addNodeLog } = useGraphStore();

  // Current chain step index (0-based), advanced by __LEGO_STEP_START__ markers.
  // Used only for status updates (active/done), NOT for log routing.
  const currentStepRef = useRef<number>(0);

  // Maps root chain step index → the node ID for that step.
  // Built during graph init; stable even after fan_out template replacement.
  const stepNodeMapRef = useRef<Map<number, string>>(new Map());

  // 1. Initialize Graph
  useEffect(() => {
    if (!graphConfig || !graphConfig.workflow) return;

    currentStepRef.current = 0;
    stepNodeMapRef.current = new Map();

    const newNodes: Node<AgentNodeData>[] = [];
    const newEdges: Edge[] = [];

    const createId = () => Math.random().toString(36).substring(2, 11);

    const parseNode = (configNode: GraphConfigNode, parentId?: string): string => {
      const id = configNode.id || createId();
      configNode.id = id;

      const type = configNode.type || 'agent';
      const label = configNode.label || configNode.name || type;
      const isGroup = ['fan_out', 'judge_loop', 'chain', 'group'].includes(type);

      newNodes.push({
        id,
        type: isGroup ? 'group' : 'agent',
        data: {
          label: label || 'Agent',
          status: 'pending',
          pattern: type as AgentNodeData['pattern'],
          logs: [],
          currentThought: configNode.instruction || configNode.task,
        },
        position: { x: 0, y: 0 },
        parentId: parentId,
        ...(type === 'judge_loop' ? { style: { width: 500, height: 300 } } : {}),
      });

      if (configNode.steps && type !== 'judge_loop') {
        let prevId: string | null = null;
        configNode.steps.forEach((step: GraphConfigNode, stepIdx: number) => {
          const stepId = parseNode(step, id);

          // Record root-level chain step → node ID for log routing.
          if (type === 'chain' && !parentId) {
            stepNodeMapRef.current.set(stepIdx, stepId);
          }

          if (type === 'chain' && prevId) {
            newEdges.push({
              id: `${prevId}-${stepId}`,
              source: prevId,
              target: stepId,
              sourceHandle: 'source-bottom',
              markerEnd: { type: MarkerType.ArrowClosed },
            });
          }
          prevId = stepId;
        });
      }

      // fan_out: one template child as placeholder until __LEGO_FANOUT_INIT__ fires.
      if (type === 'fan_out' && configNode.agent) {
        const agentCopy = { ...configNode.agent, id: undefined };
        parseNode(agentCopy, id);
      }

      if (configNode.worker && configNode.judge) {
        const workerNode = { ...configNode.worker, id: undefined };
        const judgeNode = { ...configNode.judge, id: undefined };
        const workerId = parseNode(workerNode, id);
        const judgeId = parseNode(judgeNode, id);

        newEdges.push({
          id: `${workerId}-${judgeId}`,
          source: workerId,
          target: judgeId,
          label: 'attempt',
          sourceHandle: 'source-bottom',
          targetHandle: 'target-bottom',
          type: 'smoothstep',
          markerEnd: { type: MarkerType.ArrowClosed },
          animated: true,
        });
        newEdges.push({
          id: `${judgeId}-${workerId}`,
          source: judgeId,
          target: workerId,
          label: 'critique',
          sourceHandle: 'source-top',
          type: 'smoothstep',
          style: { strokeDasharray: 5 },
          markerEnd: { type: MarkerType.ArrowClosed },
        });
      }

      return id;
    };

    parseNode(graphConfig.workflow);
    setNodes(newNodes);
    setEdges(newEdges);
  }, [graphConfig, setNodes, setEdges]);

  // 2. Process Logs
  useEffect(() => {
    if (logs.length === 0) return;

    const lastLog = logs[logs.length - 1];
    const evt = lastLog.event;

    // Snapshot nodes at the start. After mutations (addNodes/removeNodes) we
    // call useGraphStore.getState() again to get the fresh list.
    const allNodes = useGraphStore.getState().nodes;

    const getStepNodeFrom = (nodes: typeof allNodes, stepIdx: number) => {
      const nodeId = stepNodeMapRef.current.get(stepIdx);
      return nodeId ? nodes.find(n => n.id === nodeId) : undefined;
    };

    // ----------------------------------------------------------------
    // script_execution: step-marker processing + segmented log routing
    // ----------------------------------------------------------------
    if (evt.type === 'script_execution' && typeof evt.data === 'string') {

      // Advance currentStepRef first so __LEGO_FANOUT_INIT__ can look up the
      // correct group node even when both markers arrive in the same chunk.
      const stepStartMatches = [...evt.data.matchAll(/(?:^|\n)__LEGO_STEP_START__ (\d+)/g)];
      if (stepStartMatches.length > 0) {
        const maxStep = Math.max(...stepStartMatches.map(m => parseInt(m[1], 10)));
        if (maxStep > currentStepRef.current) {
          currentStepRef.current = maxStep;
        }
      }

      // --- __LEGO_FANOUT_INIT__ <count> ---
      // Replace the template node inside the fan_out group with N worker nodes.
      const fanOutMatch = [...evt.data.matchAll(/(?:^|\n)__LEGO_FANOUT_INIT__ (\d+)/g)].pop();
      if (fanOutMatch) {
        const count = parseInt(fanOutMatch[1], 10);
        const groupNode = getStepNodeFrom(allNodes, currentStepRef.current);

        if (groupNode?.type === 'group' && count > 0) {
          const templateIds = allNodes
            .filter(n => n.parentId === groupNode.id)
            .map(n => n.id);

          const workerNodes: Node<AgentNodeData>[] = Array.from({ length: count }, (_, i) => ({
            id: Math.random().toString(36).substring(2, 11),
            type: 'agent' as const,
            data: {
              label: `Worker ${i + 1}`,
              status: 'active' as const,
              pattern: 'worker' as AgentNodeData['pattern'],
              logs: [],
            },
            position: { x: 0, y: 0 },
            parentId: groupNode.id,
          }));

          removeNodes(templateIds);
          addNodes(workerNodes);
        }
      }

      // Re-read nodes after any mutations above.
      const freshNodes = useGraphStore.getState().nodes;

      // --- __LEGO_STEP_START__ <n> --- (status updates now that freshNodes includes workers)
      if (stepStartMatches.length > 0) {
        const maxStep = currentStepRef.current; // already advanced above
        const stepNode = getStepNodeFrom(freshNodes, maxStep);
        if (stepNode) updateNodeStatus(stepNode.id, 'active');

        for (let i = 0; i < maxStep; i++) {
          const prev = getStepNodeFrom(freshNodes, i);
          if (prev?.type === 'group') {
            freshNodes
              .filter(n => n.parentId === prev.id && n.data.status !== 'done')
              .forEach(child => updateNodeStatus(child.id, 'done'));
          } else if (prev && prev.data.status !== 'done') {
            updateNodeStatus(prev.id, 'done');
          }
        }
      }

      // --- __LEGO_STEP_END__ <n> --- (status updates only)
      const endMatches = [...evt.data.matchAll(/(?:^|\n)__LEGO_STEP_END__ (\d+)/g)];
      for (const m of endMatches) {
        const stepIdx = parseInt(m[1], 10);
        const stepNode = getStepNodeFrom(freshNodes, stepIdx);
        if (stepNode?.type === 'group') {
          freshNodes
            .filter(n => n.parentId === stepNode.id)
            .forEach(child => updateNodeStatus(child.id, 'done'));
        } else if (stepNode) {
          updateNodeStatus(stepNode.id, 'done');
        }
      }

      // --- Segmented log routing ---
      // The coalesced string may span multiple chain steps. Split it at each
      // __LEGO_STEP_START__ boundary so each step's content is routed only to
      // its own node — not to whichever step happens to be current at render time.
      if (stepNodeMapRef.current.size > 0) {
        const segments = extractStepSegments(evt.data);

        for (const [stepIdx, content] of segments) {
          // Synthetic LogItem: same ID as the coalesced parent + step suffix
          // so the upsert in addNodeLog updates in place rather than duplicating.
          const segLog: LogItem = {
            ...lastLog,
            id: `${lastLog.id}_s${stepIdx}`,
            event: { ...lastLog.event, data: content },
          };

          const stepNode = getStepNodeFrom(freshNodes, stepIdx);
          if (stepNode?.type === 'group') {
            freshNodes
              .filter(n => n.parentId === stepNode.id)
              .forEach(child => addNodeLog(child.id, segLog));
          } else if (stepNode) {
            addNodeLog(stepNode.id, segLog);
          }
        }
        return; // Segmented routing complete; skip default routing below.
      }
    }

    // ----------------------------------------------------------------
    // Default routing: engine-phase events (thinking, tool_start, etc.)
    // and script_execution for non-chain (no step map) workflows.
    // ----------------------------------------------------------------
    const freshNodes = useGraphStore.getState().nodes;

    const currentStepNode = getStepNodeFrom(freshNodes, currentStepRef.current);

    if (currentStepNode?.type === 'group') {
      // Broadcast engine-phase events to all group children.
      freshNodes
        .filter(n => n.parentId === currentStepNode.id)
        .forEach(child => addNodeLog(child.id, lastLog));
      return;
    }

    const targetNode = currentStepNode ?? (() => {
      const agentNodes = freshNodes.filter(n => n.type === 'agent');
      return (
        agentNodes.find(n => n.data.status === 'active') ??
        agentNodes.find(n => n.data.status === 'pending')
      );
    })();

    if (targetNode) {
      addNodeLog(targetNode.id, lastLog);

      if (evt.type === 'thinking') {
        updateNodeStatus(targetNode.id, 'active');
        if (evt.text) updateNodeThought(targetNode.id, evt.text);
      } else if (evt.type === 'tool_start') {
        updateNodeStatus(targetNode.id, 'active');
      } else if (evt.type === 'execution_result') {
        updateNodeStatus(targetNode.id, evt.exit_code === 0 ? 'done' : 'failed');
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logs, addNodeLog, addNodes, removeNodes, updateNodeStatus, updateNodeThought]);

  return null;
}
