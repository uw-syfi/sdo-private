import { useEffect, useRef } from 'react';
import { useGraphStore, AgentNodeData } from '../store/graphStore';
import { LogItem } from '../types';
import { Node, Edge, MarkerType } from 'reactflow';
import {
  computeWorkerGridLayout,
  DEFAULT_CELL_HEIGHT,
  DEFAULT_CELL_WIDTH,
} from '../utils/workerGridLayout';

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
 * Extract the numeric index from a v2 stage name ("agent_0" → 0, "fan_out_1" → 1).
 * Returns -1 if the name does not end with a valid integer.
 */
export function stageIndex(name: string): number {
  const parts = name.split('_');
  const idx = parseInt(parts[parts.length - 1], 10);
  return isNaN(idx) ? -1 : idx;
}

/**
 * Split a coalesced script_execution string into per-stage segments using
 * v2 __LEGO_TASK_START__ markers. Each segment spans from one marker to the
 * next (or end). Returns a map of stageIndex → content string.
 */
export function extractStageSegments(data: string): Map<number, string> {
  const segments = new Map<number, string>();
  const matches = [...data.matchAll(/(?:^|\n)__LEGO_TASK_START__ (\S+) \S+/g)];

  for (let i = 0; i < matches.length; i++) {
    const idx = stageIndex(matches[i][1]);
    if (idx < 0) continue;
    const start = matches[i].index!;
    const end = i + 1 < matches.length ? matches[i + 1].index! : data.length;
    segments.set(idx, data.slice(start, end));
  }

  return segments;
}

/**
 * Split a stage segment into per-worker segments using __LEGO_WORKER_START__ markers.
 * Returns a map of workerIndex → content string, or an empty map if no markers found.
 */
function extractWorkerSegments(data: string): Map<number, string> {
  const segments = new Map<number, string>();
  const matches = [...data.matchAll(/(?:^|\n)__LEGO_WORKER_START__ (\d+)/g)];

  for (let i = 0; i < matches.length; i++) {
    const workerIdx = parseInt(matches[i][1], 10);
    const start = matches[i].index!;
    const end = i + 1 < matches.length ? matches[i + 1].index! : data.length;
    segments.set(workerIdx, data.slice(start, end));
  }

  return segments;
}

interface PipelineStage {
  name: string;
  input_queue: string;
  output_queue: string | null;
}

// Edge style presets for queue states
const QUEUE_EDGE_IDLE = {
  animated: false,
  style: { stroke: '#475569', strokeWidth: 1.5 },
  labelStyle: { fill: '#94a3b8', fontSize: 10, fontFamily: 'monospace' },
  labelBgStyle: { fill: '#1e293b', fillOpacity: 0.9 },
  labelBgPadding: [4, 6] as [number, number],
  labelBgBorderRadius: 4,
};

const QUEUE_EDGE_ACTIVE = {
  animated: true,
  style: { stroke: '#10b981', strokeWidth: 2 },
  labelStyle: { fill: '#34d399', fontSize: 10, fontFamily: 'monospace' },
  labelBgStyle: { fill: '#064e3b', fillOpacity: 0.95 },
  labelBgPadding: [4, 6] as [number, number],
  labelBgBorderRadius: 4,
};

export function GraphAdapter({ logs, graphConfig }: GraphAdapterProps) {
  const { setNodes, setEdges, addNodes, removeNodes, updateNodeStatus, updateNodeThought, addNodeLog, updateEdge } = useGraphStore();

  // Current chain step index (0-based), advanced by __LEGO_TASK_START__ markers.
  // Used only for status updates (active/done), NOT for log routing.
  const currentStepRef = useRef<number>(0);

  // Maps root chain step index → the node ID for that step.
  // Built during graph init; stable even after fan_out template replacement.
  const stepNodeMapRef = useRef<Map<number, string>>(new Map());

  // Populated from __LEGO_PIPELINE_INIT__: maps queue name → edge ID.
  const queueEdgeMapRef = useRef<Map<string, string>>(new Map());

  // Pipeline stage list from __LEGO_PIPELINE_INIT__, used for queue→edge routing.
  const pipelineStagesRef = useRef<PipelineStage[]>([]);

  // 1. Initialize Graph
  useEffect(() => {
    if (!graphConfig || !graphConfig.workflow) return;

    currentStepRef.current = 0;
    stepNodeMapRef.current = new Map();
    queueEdgeMapRef.current = new Map();
    pipelineStagesRef.current = [];

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
              sourceHandle: 'source-right',
              targetHandle: 'target-left',
              type: 'smoothstep',
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
          sourceHandle: 'source-right',
          targetHandle: 'target-left',
          type: 'smoothstep',
          markerEnd: { type: MarkerType.ArrowClosed },
          animated: true,
        });
        newEdges.push({
          id: `${judgeId}-${workerId}`,
          source: judgeId,
          target: workerId,
          label: 'critique',
          sourceHandle: 'source-left',
          targetHandle: 'target-right',
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

      // --- __LEGO_PIPELINE_INIT__ <json> ---
      // Wire queue names onto inter-stage edges so they can be animated.
      // Processed first so the queue map is ready before TASK_* handlers run.
      const pipelineInitMatch = evt.data.match(/(?:^|\n)__LEGO_PIPELINE_INIT__ (.+)/);
      if (pipelineInitMatch) {
        try {
          const stages: PipelineStage[] = JSON.parse(pipelineInitMatch[1]);
          pipelineStagesRef.current = stages;
          const newQueueEdgeMap = new Map<string, string>();

          for (let i = 0; i < stages.length - 1; i++) {
            const { output_queue } = stages[i];
            if (!output_queue) continue;
            const srcNodeId = stepNodeMapRef.current.get(stageIndex(stages[i].name));
            const dstNodeId = stepNodeMapRef.current.get(stageIndex(stages[i + 1].name));
            if (srcNodeId && dstNodeId) {
              const edgeId = `${srcNodeId}-${dstNodeId}`;
              newQueueEdgeMap.set(output_queue, edgeId);
              updateEdge(edgeId, { label: output_queue, ...QUEUE_EDGE_IDLE });
            }
          }

          queueEdgeMapRef.current = newQueueEdgeMap;
        } catch {
          // malformed JSON — ignore
        }
      }

      // Advance currentStepRef first so __LEGO_FANOUT_INIT__ can look up the
      // correct group node even when both markers arrive in the same chunk.
      const taskStartMatches = [...evt.data.matchAll(/(?:^|\n)__LEGO_TASK_START__ (\S+) \S+/g)];
      if (taskStartMatches.length > 0) {
        const maxIdx = Math.max(...taskStartMatches.map(m => stageIndex(m[1])));
        if (maxIdx >= 0 && maxIdx > currentStepRef.current) {
          currentStepRef.current = maxIdx;
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

          const { positions, bounds } = computeWorkerGridLayout(count);
          const workerNodes: Node<AgentNodeData>[] = Array.from({ length: count }, (_, i) => ({
            id: Math.random().toString(36).substring(2, 11),
            type: 'agent' as const,
            data: {
              label: `Worker ${i + 1}`,
              status: 'active' as const,
              pattern: 'worker' as AgentNodeData['pattern'],
              logs: [],
            },
            position: positions[i],
            parentId: groupNode.id,
            style: { width: DEFAULT_CELL_WIDTH, height: DEFAULT_CELL_HEIGHT },
          }));

          removeNodes(templateIds);
          addNodes(workerNodes);
          useGraphStore.getState().setNodes(
            useGraphStore.getState().nodes.map((n) =>
              n.id === groupNode.id
                ? { ...n, style: { ...n.style, width: bounds.width, height: bounds.height } }
                : n,
            ),
          );
        }
      }

      // Re-read nodes after any mutations above.
      const freshNodes = useGraphStore.getState().nodes;

      // --- __LEGO_TASK_START__ <name> <id> --- mark stage active, prior stages done
      if (taskStartMatches.length > 0) {
        const maxIdx = currentStepRef.current;
        const stepNode = getStepNodeFrom(freshNodes, maxIdx);
        if (stepNode) updateNodeStatus(stepNode.id, 'active');

        for (let i = 0; i < maxIdx; i++) {
          const prev = getStepNodeFrom(freshNodes, i);
          if (prev?.type === 'group') {
            freshNodes
              .filter(n => n.parentId === prev.id && n.data.status !== 'done')
              .forEach(child => updateNodeStatus(child.id, 'done'));
          } else if (prev && prev.data.status !== 'done') {
            updateNodeStatus(prev.id, 'done');
          }
        }

        // A task was dequeued → its input queue is now draining; de-animate edge.
        for (const m of taskStartMatches) {
          const stage = pipelineStagesRef.current.find(s => s.name === m[1]);
          if (stage) {
            const edgeId = queueEdgeMapRef.current.get(stage.input_queue);
            if (edgeId) updateEdge(edgeId, QUEUE_EDGE_IDLE);
          }
        }
      }

      // --- __LEGO_TASK_DONE__ <name> <id> --- mark stage done
      const taskDoneMatches = [...evt.data.matchAll(/(?:^|\n)__LEGO_TASK_DONE__ (\S+) \S+/g)];
      for (const m of taskDoneMatches) {
        const idx = stageIndex(m[1]);
        const stepNode = getStepNodeFrom(freshNodes, idx);
        if (stepNode?.type === 'group') {
          freshNodes
            .filter(n => n.parentId === stepNode.id)
            .forEach(child => updateNodeStatus(child.id, 'done'));
        } else if (stepNode) {
          updateNodeStatus(stepNode.id, 'done');
        }

        // Task output was enqueued → animate the outbound edge.
        const stage = pipelineStagesRef.current.find(s => s.name === m[1]);
        if (stage?.output_queue) {
          const edgeId = queueEdgeMapRef.current.get(stage.output_queue);
          if (edgeId) updateEdge(edgeId, QUEUE_EDGE_ACTIVE);
        }
      }

      // --- __LEGO_STAGE_IDLE__ <name> --- queue drained; mark done if not already
      const stageIdleMatches = [...evt.data.matchAll(/(?:^|\n)__LEGO_STAGE_IDLE__ (\S+)/g)];
      for (const m of stageIdleMatches) {
        const idx = stageIndex(m[1]);
        const stepNode = getStepNodeFrom(freshNodes, idx);
        if (stepNode && stepNode.data.status !== 'done') {
          if (stepNode.type === 'group') {
            freshNodes
              .filter(n => n.parentId === stepNode.id && n.data.status !== 'done')
              .forEach(child => updateNodeStatus(child.id, 'done'));
          } else {
            updateNodeStatus(stepNode.id, 'done');
          }
        }
      }

      // --- Segmented log routing ---
      // Split at each __LEGO_TASK_START__ boundary so each stage's content is
      // routed only to its own node, not whichever stage is current at render time.
      if (stepNodeMapRef.current.size > 0) {
        const segments = extractStageSegments(evt.data);

        for (const [stageIdx, content] of segments) {
          // Synthetic LogItem: same ID as the coalesced parent + stage suffix
          // so the upsert in addNodeLog updates in place rather than duplicating.
          const segLog: LogItem = {
            ...lastLog,
            id: `${lastLog.id}_s${stageIdx}`,
            event: { ...lastLog.event, data: content },
          };

          const stepNode = getStepNodeFrom(freshNodes, stageIdx);
          if (stepNode?.type === 'group') {
            const workerSegments = extractWorkerSegments(content);
            if (workerSegments.size > 0) {
              // Route each worker's captured output to its specific child node.
              const children = freshNodes.filter(n => n.parentId === stepNode.id);
              for (const [workerIdx, workerContent] of workerSegments) {
                const workerNode = children[workerIdx];
                if (workerNode) {
                  addNodeLog(workerNode.id, {
                    ...lastLog,
                    id: `${lastLog.id}_s${stageIdx}_w${workerIdx}`,
                    event: { ...lastLog.event, data: workerContent },
                  });
                }
              }
            } else {
              // No worker markers — broadcast to all children.
              freshNodes
                .filter(n => n.parentId === stepNode.id)
                .forEach(child => addNodeLog(child.id, segLog));
            }
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
  }, [logs, addNodeLog, addNodes, removeNodes, updateNodeStatus, updateNodeThought, updateEdge]);

  return null;
}
