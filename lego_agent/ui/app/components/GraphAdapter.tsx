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

export function GraphAdapter({ logs, graphConfig }: GraphAdapterProps) {
  const { setNodes, setEdges, updateNodeStatus, updateNodeThought, addNodeLog } = useGraphStore();

  // Tracks which chain step is currently executing (0-indexed), updated via
  // __LEGO_STEP_START__ markers emitted by Chain.run() in the subprocess stdout.
  const currentStepRef = useRef<number>(0);

  // 1. Initialize Graph
  useEffect(() => {
    if (!graphConfig || !graphConfig.workflow) return;

    // Reset step index on each new graph
    currentStepRef.current = 0;

    const newNodes: Node<AgentNodeData>[] = [];
    const newEdges: Edge[] = [];

    const createId = () => Math.random().toString(36).substring(2, 11);
    
    const parseNode = (configNode: GraphConfigNode, parentId?: string): string => {
       const id = configNode.id || createId(); 
       // Store generated ID back to configNode to reuse if needed (mutating local copy logic)
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
            currentThought: configNode.instruction || configNode.task
         },
         position: { x: 0, y: 0 },
         parentNode: parentId,
         ...(type === 'judge_loop' ? { style: { width: 500, height: 300 } } : {})
       });
       
       // For judge_loop, we skip steps processing to avoid redundant edges if worker/judge are also in steps
       if (configNode.steps && type !== 'judge_loop') {
           let prevId: string | null = null;
           configNode.steps.forEach((step: GraphConfigNode) => {
               const stepId = parseNode(step, id);
               if (type === 'chain' && prevId) {
                  newEdges.push({ 
                      id: `${prevId}-${stepId}`, 
                      source: prevId, 
                      target: stepId,
                      sourceHandle: 'source-bottom',
                      markerEnd: { type: MarkerType.ArrowClosed }
                  });
               }
               prevId = stepId;
           });
       }
       
       // For fan_out, render the template agent as a single child representative
       if (type === 'fan_out' && configNode.agent) {
           const agentCopy = { ...configNode.agent, id: undefined };
           parseNode(agentCopy, id);
       }

       if (configNode.worker && configNode.judge) {
           // Clone to ensure unique IDs if the agent object is reused
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
              animated: true
          });
           newEdges.push({ 
               id: `${judgeId}-${workerId}`, 
               source: judgeId, 
               target: workerId, 
               label: 'critique', 
               sourceHandle: 'source-top',
               type: 'smoothstep',
               style: { strokeDasharray: 5 },
               markerEnd: { type: MarkerType.ArrowClosed }
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

    // Use getState() to read current nodes without adding them to the dep array
    // (which would cause an infinite update loop).
    const agentNodes = useGraphStore.getState().nodes.filter(n => n.type === 'agent');

    // Detect chain step markers emitted by Chain.run() in the subprocess stdout.
    // Script execution events get coalesced, so the data string grows and may
    // contain multiple markers. We use the *highest* step index seen so far
    // (never regress) to advance the active node correctly.
    if (evt.type === 'script_execution' && typeof evt.data === 'string') {
      const startMatches = [...evt.data.matchAll(/(?:^|\n)__LEGO_STEP_START__ (\d+)/g)];
      if (startMatches.length > 0) {
        const maxStep = Math.max(...startMatches.map(m => parseInt(m[1], 10)));
        if (maxStep > currentStepRef.current) {
          currentStepRef.current = maxStep;
          if (maxStep < agentNodes.length) {
            updateNodeStatus(agentNodes[maxStep].id, 'active');
            // Mark all previous steps done when we advance past them
            for (let i = 0; i < maxStep; i++) {
              if (agentNodes[i].data.status !== 'done') {
                updateNodeStatus(agentNodes[i].id, 'done');
              }
            }
          }
        }
      }

      const endMatches = [...evt.data.matchAll(/(?:^|\n)__LEGO_STEP_END__ (\d+)/g)];
      for (const m of endMatches) {
        const stepIdx = parseInt(m[1], 10);
        if (stepIdx < agentNodes.length) {
          updateNodeStatus(agentNodes[stepIdx].id, 'done');
        }
      }
    }

    // Route the log to the node for the current chain step, falling back to the
    // first active or pending agent node for engine-phase events (thinking, tools).
    const targetNode =
      agentNodes[currentStepRef.current] ||
      agentNodes.find(n => n.data.status === 'active') ||
      agentNodes.find(n => n.data.status === 'pending');

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
  }, [logs, addNodeLog, updateNodeStatus, updateNodeThought]);

  return null;
}
