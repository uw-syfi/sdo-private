import { useState, useEffect, useRef, useCallback } from 'react';
import { AgentEvent, LogItem } from '../types';
import { QueueSnapshot } from '@/lib/queueSnapshot';

export function useLegoAgent() {
  const [logs, setLogs] = useState<LogItem[]>([]);
  // Per-agent log streams keyed by agent_id (e.g. "worker_0").
  // Populated only for messages that carry an agent_id field.
  const [agentLogs, setAgentLogs] = useState<Record<string, LogItem[]>>({});
  const [status, setStatus] = useState<'disconnected' | 'connecting' | 'connected' | 'running'>('disconnected');
  const [pendingQuestions, setPendingQuestions] = useState<string[] | null>(null);
  const [cwd, setCwd] = useState<string>('.');
  const [dirOptions, setDirOptions] = useState<string[]>([]);
  const [model, setModel] = useState<string | undefined>(undefined);
  const [thinkingBudget, setThinkingBudget] = useState<number | undefined>(undefined);
  const [graphConfig, setGraphConfig] = useState<any>(null);
  const [queueSnapshot, setQueueSnapshot] = useState<QueueSnapshot | null>(null);
  const ws = useRef<WebSocket | null>(null);

  const addLog = useCallback((event: AgentEvent) => {
    setLogs(prev => [...prev, {
      id: Math.random().toString(36).substring(7),
      event,
      timestamp: Date.now()
    }]);
  }, []);

  const handleEvent = useCallback((event: AgentEvent) => {
    // Queue snapshots are silent state updates — never shown in logs.
    if (event.type === 'queue_snapshot' && event.config) {
      setQueueSnapshot(event.config as QueueSnapshot);
      return;
    }

    // Route events with agent_id to per-agent streams (e.g. FanOut workers).
    // They also flow into the global logs so the sidebar log panel stays complete.
    if (event.agent_id !== undefined) {
      const agentId = event.agent_id;
      setAgentLogs(prev => {
        const existing = prev[agentId] ?? [];
        // Coalesce consecutive thinking chunks per agent.
        if (event.type === 'thinking' && existing.length > 0) {
          const last = existing[existing.length - 1];
          if (last.event.type === 'thinking') {
            return {
              ...prev,
              [agentId]: [
                ...existing.slice(0, -1),
                { ...last, event: { ...last.event, text: (last.event.text ?? '') + (event.text ?? '') } },
              ],
            };
          }
        }
        return {
          ...prev,
          [agentId]: [...existing, { id: Math.random().toString(36).substring(7), event, timestamp: Date.now() }],
        };
      });
    }

    setLogs(prev => {
        // Coalesce thinking events
        if (event.type === 'thinking' && prev.length > 0) {
            const lastLog = prev[prev.length - 1];
            if (lastLog.event.type === 'thinking') {
                return [
                    ...prev.slice(0, -1),
                    {
                        ...lastLog,
                        event: {
                            ...lastLog.event,
                            text: (lastLog.event.text || '') + (event.text || '')
                        }
                    }
                ];
            }
        }
        
        // Coalesce execution stream events
        if (event.type === 'script_execution' && prev.length > 0) {
            const lastLog = prev[prev.length - 1];
            if (lastLog.event.type === 'script_execution' && lastLog.event.stream === event.stream) {
                 return [
                    ...prev.slice(0, -1),
                    {
                        ...lastLog,
                        event: {
                            ...lastLog.event,
                            data: (lastLog.event.data || '') + '\n' + (event.data || '')
                        }
                    }
                ];
            }
        }

        return [...prev, {
            id: Math.random().toString(36).substring(7),
            event,
            timestamp: Date.now()
        }];
    });

    if (event.type === 'question' && event.questions) {
      setPendingQuestions(event.questions);
    } else if (event.type === 'execution_result') {
      setStatus('connected'); // Back to idle/connected state
    } else if (event.type === 'init' && event.cwd) {
        setCwd(event.cwd);
        if (event.model) setModel(event.model);
        if (event.thinking_budget !== undefined) setThinkingBudget(event.thinking_budget);
        
        // Check for saved CWD
        const savedCwd = localStorage.getItem('lego_agent_cwd');
        if (savedCwd && savedCwd !== event.cwd && ws.current) {
             ws.current.send(JSON.stringify({ type: 'validate_path', path: savedCwd }));
        }
    } else if (event.type === 'path_validation') {
        if (event.valid && event.path) {
            setCwd(event.path);
            setLogs(prev => [...prev, {
                id: Math.random().toString(36).substring(7),
                event: { type: 'log', message: `Restored working directory: ${event.path}`, level: 'info' },
                timestamp: Date.now()
            }]);
        } else if (event.path) {
            // Invalid path, clear storage
             localStorage.removeItem('lego_agent_cwd');
             setLogs(prev => [...prev, {
                id: Math.random().toString(36).substring(7),
                event: { type: 'log', message: `Saved directory not found: ${event.path}, using default`, level: 'error' },
                timestamp: Date.now()
            }]);
        }
    } else if (event.type === 'dir_options' && event.options) {
        setDirOptions(event.options);
    } else if (event.type === 'graph' && event.config) {
        setGraphConfig(event.config);
        addLog({ type: 'log', message: 'Graph execution plan received', level: 'info' });
    }

  }, [addLog]);

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return;

    setStatus('connecting');
    // Assume server is on port 8000
    const socket = new WebSocket('ws://localhost:8000/ws');

    socket.onopen = () => {
      setStatus('connected');
      addLog({ type: 'log', message: 'Connected to LegoAgent Server', level: 'success' });
    };

    socket.onmessage = (event) => {
      try {
        const data: AgentEvent = JSON.parse(event.data);
        handleEvent(data);
      } catch {
        console.error("Failed to parse message", event.data);
      }
    };

    socket.onclose = () => {
      setStatus('disconnected');
      addLog({ type: 'log', message: 'Disconnected from server', level: 'error' });
      ws.current = null;
    };

    ws.current = socket;
  }, [addLog, handleEvent]);

  const sendPrompt = (prompt: string, workDir: string) => {
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) {
      // Try to connect if not connected?
      // For now assume connected.
      return;
    }
    
    // Clear logs on new run
    setLogs([]); 
    setStatus('running');
    setQueueSnapshot(null);
    
    // Save CWD
    localStorage.setItem('lego_agent_cwd', workDir);
    
    ws.current.send(JSON.stringify({ type: 'start', prompt, work_dir: workDir }));
  };

  const stopAgent = () => {
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) return;
    
    ws.current.send(JSON.stringify({ type: 'stop' }));
    setStatus('connected');
    setPendingQuestions(null);
    addLog({ type: 'log', message: 'Stopping agent...', level: 'info' });
  };

  const sendAnswers = (answers: string[]) => {
    if (!ws.current) return;
    ws.current.send(JSON.stringify({ type: 'answer', answers }));
    setPendingQuestions(null);
    // Log the answer for visibility
    answers.forEach((ans, i) => {
       addLog({ type: 'log', message: `Answer ${i+1}: ${ans}`, level: 'info' });
    });
  };

  const listDirs = (path: string) => {
      if (!ws.current) return;
      ws.current.send(JSON.stringify({ type: 'list_dirs', path }));
  };

  const updateCwd = (newCwd: string) => {
      setCwd(newCwd);
      localStorage.setItem('lego_agent_cwd', newCwd);
  };

  useEffect(() => {
    // eslint-disable-next-line
    connect();
    return () => {
      ws.current?.close();
    };
  }, [connect]);

  return {
    logs,
    agentLogs,
    status,
    pendingQuestions,
    sendPrompt,
    sendAnswers,
    stopAgent,
    connect,
    cwd,
    dirOptions,
    listDirs,
    updateCwd,
    model,
    thinkingBudget,
    graphConfig,
    queueSnapshot
  };
}
