import { useState, useEffect, useRef, useCallback } from 'react';
import { AgentEvent, LogItem } from '../types';

export function useLegoAgent() {
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [status, setStatus] = useState<'disconnected' | 'connecting' | 'connected' | 'running'>('disconnected');
  const [pendingQuestions, setPendingQuestions] = useState<string[] | null>(null);
  const ws = useRef<WebSocket | null>(null);

  const addLog = useCallback((event: AgentEvent) => {
    setLogs(prev => [...prev, {
      id: Math.random().toString(36).substring(7),
      event,
      timestamp: Date.now()
    }]);
  }, []);

  const handleEvent = useCallback((event: AgentEvent) => {
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
    }
  }, []);

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

  const sendPrompt = (prompt: string) => {
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) {
      // Try to connect if not connected?
      // For now assume connected.
      return;
    }
    
    // Clear logs on new run
    setLogs([]); 
    setStatus('running');
    ws.current.send(JSON.stringify({ type: 'start', prompt }));
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

  useEffect(() => {
    // eslint-disable-next-line
    connect();
    return () => {
      ws.current?.close();
    };
  }, [connect]);

  return {
    logs,
    status,
    pendingQuestions,
    sendPrompt,
    sendAnswers,
    stopAgent,
    connect
  };
}
