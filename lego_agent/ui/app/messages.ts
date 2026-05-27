// messages.ts — Canonical WebSocket message schema for LegoAgent.
//
// Mirrors lego_agent/messages.py exactly. Every type here has a 1:1 counterpart
// in Python. When adding or changing a message type, update both files together.
//
// Pattern: discriminated union on the `type` field, identical to Rust's
//   enum ServerMsg { Init { cwd: string, ... }, Thinking { text: string }, ... }
// TypeScript narrows the union automatically when you check `msg.type`.

// ── Server → Client ──────────────────────────────────────────────────────────
//
// agent_id is present on messages emitted by runtime workers (FanOut, etc.)
// and absent on messages emitted directly by the server (init, question, ...).

export type InitMsg = {
  type: "init";
  cwd: string;
  model: string;
  thinking_budget: number;
  agent_id?: string;
};

export type ThinkingMsg = {
  type: "thinking";
  text: string;
  agent_id?: string;
};

export type ToolStartMsg = {
  type: "tool_start";
  name: string;
  input: string;
  agent_id?: string;
};

export type ToolEndMsg = {
  type: "tool_end";
  name: string;
  output: string;
  status: "success" | "error";
  agent_id?: string;
};

export type LogMsg = {
  type: "log";
  message: string;
  level: "info" | "error" | "success";
  agent_id?: string;
};

export type ScriptExecutionMsg = {
  type: "script_execution";
  stream: "stdout" | "stderr";
  data: string;
  agent_id?: string;
};

export type ExecutionResultMsg = {
  type: "execution_result";
  exit_code: number;
  agent_id?: string;
};

export type QuestionMsg = {
  type: "question";
  questions: string[];
  agent_id?: string;
};

export type DirOptionsMsg = {
  type: "dir_options";
  options: string[];
  agent_id?: string;
};

export type PathValidationMsg = {
  type: "path_validation";
  path: string;
  valid: boolean;
  agent_id?: string;
};

export type GraphMsg = {
  type: "graph";
  config: unknown;
  agent_id?: string;
};

/** All messages the server can send to the client. */
export type ServerMsg =
  | InitMsg
  | ThinkingMsg
  | ToolStartMsg
  | ToolEndMsg
  | LogMsg
  | ScriptExecutionMsg
  | ExecutionResultMsg
  | QuestionMsg
  | DirOptionsMsg
  | PathValidationMsg
  | GraphMsg;

// ── Client → Server ──────────────────────────────────────────────────────────

export type StartMsg = {
  type: "start";
  prompt: string;
  work_dir: string;
};

export type StopMsg = {
  type: "stop";
};

export type AnswerMsg = {
  type: "answer";
  answers: string[];
};

export type ListDirsMsg = {
  type: "list_dirs";
  path: string;
};

export type ValidatePathMsg = {
  type: "validate_path";
  path: string;
};

/** All messages the client can send to the server. */
export type ClientMsg =
  | StartMsg
  | StopMsg
  | AnswerMsg
  | ListDirsMsg
  | ValidatePathMsg;
