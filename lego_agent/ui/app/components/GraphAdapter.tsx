import { useEffect } from 'react';
import { useGraphStore, AgentNodeData } from '../store/graphStore';
import { LogItem } from '../types';
import { Node, Edge, MarkerType } from 'reactflow';

interface GraphAdapterProps {
  logs: LogItem[];
  graphConfig: any;
}

export function GraphAdapter({ logs, graphConfig }: GraphAdapterProps) {
  const { setNodes, setEdges, nodes, updateNodeStatus, updateNodeThought, addNodeLog } = useGraphStore();

  // 1. Initialize Graph
  useEffect(() => {
    if (!graphConfig || !graphConfig.workflow) return;

    const newNodes: Node<AgentNodeData>[] = [];
    const newEdges: Edge[] = [];
    
    const createId = () => Math.random().toString(36).substr(2, 9);
    
    const parseNode = (configNode: any, parentId?: string): string => {
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
            label,
            status: 'pending',
            pattern: type as any,
            logs: [],
            currentThought: configNode.instruction || configNode.task
         },
         position: { x: 0, y: 0 },
         parentNode: parentId,
         ...(isGroup ? { style: { width: type === 'judge_loop' ? 500 : 400, height: 300 } } : {})
       });
       
       // For judge_loop, we skip steps processing to avoid redundant edges if worker/judge are also in steps
       if (configNode.steps && type !== 'judge_loop') {
           let prevId: string | null = null;
           configNode.steps.forEach((step: any) => {
               const stepId = parseNode(step, id);
               if (type === 'chain' && prevId) {
                   newEdges.push({ 
                       id: `${prevId}-${stepId}`, 
                       source: prevId, 
                       target: stepId,
                       markerEnd: { type: MarkerType.ArrowClosed }
                   });
               }
               prevId = stepId;
           });
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
    
    // Attempt to match node by name
    if (evt.name) {
        // We look for a node where data.label matches evt.name
        const node = nodes.find(n => n.data.label === evt.name);
        
        if (node) {
            addNodeLog(node.id, lastLog);
            
            if (evt.type === 'thinking') {
                updateNodeStatus(node.id, 'active');
                // Only update thought if it's a significant chunk or we throttle it
                // For now, just append/replace
                if (evt.text) updateNodeThought(node.id, evt.text);
            } else if (evt.type === 'tool_start') {
                updateNodeStatus(node.id, 'active');
                updateNodeThought(node.id, `Using tool: ${evt.name || 'unknown'}`);
            } else if (evt.type === 'tool_end') {
                // Keep active? 
            } else if (evt.type === 'execution_result') {
                updateNodeStatus(node.id, 'done');
            }
        }
    }
    // We intentionally omit 'nodes' from dependency array to avoid infinite loop
    // We only want to process when 'logs' actually changes (new log arrived)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logs, addNodeLog, updateNodeStatus, updateNodeThought]);

  return null;
}
