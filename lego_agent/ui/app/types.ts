export interface AgentEvent {
  type: "thinking" | "tool_start" | "tool_end" | "question" | "log" | "script_execution" | "execution_result";
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
}

export interface LogItem {
  id: string;
  event: AgentEvent;
  timestamp: number;
}
