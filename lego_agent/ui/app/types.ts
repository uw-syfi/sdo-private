export interface AgentEvent {
  type: "thinking" | "tool_start" | "tool_end" | "question" | "log" | "script_execution" | "execution_result" | "init" | "dir_options" | "path_validation" | "graph";
  // specific fields
  text?: string;
  name?: string;
  input?: string;
  output?: string;
  status?: string;
  questions?: string[];
  message?: string;
  level?: "info" | "error" | "success";
  stream?: "stdout" | "stderr";
  data?: string;
  exit_code?: number;
  cwd?: string;
  options?: string[];
  model?: string;
  thinking_budget?: number;
  path?: string;
  valid?: boolean;
  config?: any;
}

export interface LogItem {
  id: string;
  event: AgentEvent;
  timestamp: number;
}
