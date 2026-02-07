import { useState, useEffect, useRef } from 'react';
import { LogItem, AgentEvent } from '../types';

interface DemoConfig {
  graphConfig: any;
  logs: LogItem[];
}

const TEMPLATES = {
  fan_out: {
    graphConfig: {
      workflow: {
        type: 'fan_out',
        id: 'root',
        name: 'Supervisor',
        instruction: 'Coordinate research on LLM agents',
        steps: [
          { id: 'w1', name: 'WebSearch', type: 'agent', instruction: 'Search for latest papers' },
          { id: 'w2', name: 'GitHubScanner', type: 'agent', instruction: 'Scan repositories' },
          { id: 'w3', name: 'BlogReader', type: 'agent', instruction: 'Read tech blogs' }
        ]
      }
    },
    script: [
      { t: 0, name: 'Supervisor', type: 'thinking', text: 'I need to gather information from multiple sources.' },
      { t: 1000, name: 'Supervisor', type: 'tool_start', text: 'Delegating tasks to workers...' },
      { t: 1500, name: 'WebSearch', type: 'thinking', text: 'Searching Google for "LLM agent patterns"...' },
      { t: 1600, name: 'GitHubScanner', type: 'thinking', text: 'Querying GitHub API for "langgraph"...' },
      { t: 1700, name: 'BlogReader', type: 'thinking', text: 'Checking Hackernews...' },
      { t: 2500, name: 'WebSearch', type: 'tool_start', text: 'Executing search...' },
      { t: 3000, name: 'WebSearch', type: 'tool_end', text: 'Found 15 relevant results.' },
      { t: 3500, name: 'GitHubScanner', type: 'tool_start', text: 'Cloning repo...' },
      { t: 4000, name: 'BlogReader', type: 'thinking', text: 'Found interesting article on auto-gpt.' },
      { t: 5000, name: 'WebSearch', type: 'thinking', text: 'Summarizing search results...' },
      { t: 6000, name: 'GitHubScanner', type: 'tool_end', text: 'Analysis complete.' },
      { t: 7000, name: 'Supervisor', type: 'thinking', text: 'Received reports from all workers. Synthesizing...' },
      { t: 8000, name: 'Supervisor', type: 'execution_result', text: 'Final Report: LLM Agents are evolving rapidly...' }
    ]
  },
  judge_loop: {
    graphConfig: {
      workflow: {
        type: 'judge_loop',
        id: 'root',
        name: 'Orchestrator',
        instruction: 'Ensure code quality',
        worker: { id: 'g1', name: 'Generator', type: 'agent', instruction: 'Write Python script' },
        judge: { id: 'j1', name: 'Critic', type: 'agent', instruction: 'Review code' }
      }
    },
    script: [
      { t: 0, name: 'Orchestrator', type: 'thinking', text: 'Starting coding task...' },
      { t: 500, name: 'Generator', type: 'thinking', text: 'Drafting initial solution using bubble sort...' },
      { t: 2000, name: 'Generator', type: 'tool_start', text: 'Writing file sort.py' },
      { t: 3000, name: 'Generator', type: 'tool_end', text: 'File written.' },
      { t: 3500, name: 'Critic', type: 'thinking', text: 'Reviewing code for complexity...' },
      { t: 4500, name: 'Critic', type: 'thinking', text: 'O(n^2) is not acceptable. Rejecting.' },
      { t: 5000, name: 'Orchestrator', type: 'thinking', text: 'Critique received. Retrying...' },
      { t: 6000, name: 'Generator', type: 'thinking', text: 'Understood. Switching to Merge Sort.' },
      { t: 7500, name: 'Generator', type: 'tool_start', text: 'Rewriting sort.py' },
      { t: 8500, name: 'Generator', type: 'tool_end', text: 'File updated.' },
      { t: 9000, name: 'Critic', type: 'thinking', text: 'Reviewing code...' },
      { t: 10000, name: 'Critic', type: 'thinking', text: 'Looks good. O(n log n). Approved.' },
      { t: 11000, name: 'Orchestrator', type: 'execution_result', text: 'Task completed successfully.' }
    ]
  },
  sequential: {
    graphConfig: {
      workflow: {
        type: 'chain',
        id: 'root',
        name: 'Pipeline',
        instruction: 'Process data',
        steps: [
          { id: 's1', name: 'Extractor', type: 'agent' },
          { id: 's2', name: 'Transformer', type: 'agent' },
          { id: 's3', name: 'Loader', type: 'agent' }
        ]
      }
    },
    script: [
      { t: 0, name: 'Extractor', type: 'thinking', text: 'Reading raw data...' },
      { t: 1500, name: 'Extractor', type: 'execution_result', text: 'Data extracted.' },
      { t: 2000, name: 'Transformer', type: 'thinking', text: 'Normalizing format...' },
      { t: 3500, name: 'Transformer', type: 'execution_result', text: 'Data transformed.' },
      { t: 4000, name: 'Loader', type: 'thinking', text: 'Pushing to database...' },
      { t: 5500, name: 'Loader', type: 'execution_result', text: 'Data loaded.' }
    ]
  },
  hierarchical: {
    graphConfig: {
      workflow: {
        type: 'group',
        id: 'root',
        name: 'Manager',
        instruction: 'Build web app',
        steps: [
          { 
            type: 'group', id: 'fe', name: 'Frontend', 
            steps: [
                { id: 'fe1', name: 'React', type: 'agent' }, 
                { id: 'fe2', name: 'Tailwind', type: 'agent' }
            ] 
          },
          { 
            type: 'group', id: 'be', name: 'Backend', 
            steps: [
                { id: 'be1', name: 'FastAPI', type: 'agent' }, 
                { id: 'be2', name: 'Postgres', type: 'agent' }
            ] 
          }
        ]
      }
    },
    script: [
      { t: 0, name: 'Manager', type: 'thinking', text: 'Initializing project...' },
      { t: 1000, name: 'Frontend', type: 'thinking', text: 'Setting up UI scaffold' },
      { t: 1200, name: 'Backend', type: 'thinking', text: 'Designing Schema' },
      { t: 2000, name: 'React', type: 'thinking', text: 'Creating components...' },
      { t: 2200, name: 'Postgres', type: 'thinking', text: 'Writing migrations...' },
      { t: 3000, name: 'FastAPI', type: 'thinking', text: 'Defining endpoints...' },
      { t: 3500, name: 'Tailwind', type: 'thinking', text: 'Configuring theme...' },
      { t: 5000, name: 'React', type: 'tool_start', text: 'npm install' },
      { t: 5500, name: 'FastAPI', type: 'tool_start', text: 'pip install' },
      { t: 7000, name: 'Manager', type: 'thinking', text: 'Monitoring progress...' }
    ]
  }
};

export const useDemoSimulation = (patternId: string | null) => {
  const [state, setState] = useState<DemoConfig>({ graphConfig: null, logs: [] });
  const [isPlaying, setIsPlaying] = useState(false);
  
  // Use a ref to track startTime so we can handle pausing/resuming if needed, 
  // but for now just simple restart on pattern change
  const startTimeRef = useRef<number>(0);
  const timeoutsRef = useRef<NodeJS.Timeout[]>([]);

  useEffect(() => {
    // Cleanup previous timers
    timeoutsRef.current.forEach(clearTimeout);
    timeoutsRef.current = [];
    
    if (!patternId || !TEMPLATES[patternId as keyof typeof TEMPLATES]) {
      setState({ graphConfig: null, logs: [] });
      setIsPlaying(false);
      return;
    }

    const template = TEMPLATES[patternId as keyof typeof TEMPLATES];
    
    // Reset state
    setState({
      graphConfig: template.graphConfig,
      logs: [] // Clear logs
    });
    setIsPlaying(true);
    
    // Schedule events
    const baseTime = Date.now();
    
    template.script.forEach((step) => {
        const timeout = setTimeout(() => {
            const logItem: LogItem = {
                id: Math.random().toString(36),
                timestamp: Date.now(),
                event: {
                    type: step.type as any,
                    name: step.name,
                    text: step.text,
                    // Mock other fields if needed
                }
            };
            
            setState(prev => ({
                ...prev,
                logs: [...prev.logs, logItem]
            }));
            
        }, step.t);
        
        timeoutsRef.current.push(timeout);
    });
    
    return () => {
        timeoutsRef.current.forEach(clearTimeout);
    };
  }, [patternId]);

  return state;
};
