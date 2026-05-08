export interface QueueTaskSnapshot {
  id: string;
  type: string;
  label: string;
}

export interface QueueStageSnapshot {
  name: string;
  input_queue: string;
  output_queue: string | null;
  total: number;
  active_total: number;
  truncated: boolean;
  active_truncated: boolean;
  tasks: QueueTaskSnapshot[];
  active_tasks: QueueTaskSnapshot[];
}

export interface QueueSnapshot {
  max_concurrent_workers: number;
  stages: QueueStageSnapshot[];
}

const QUEUE_SNAPSHOT_PATTERN = /(?:^|\n)__LEGO_QUEUE_SNAPSHOT__ (.+)/g;

export function extractQueueSnapshot(data: string): QueueSnapshot | null {
  QUEUE_SNAPSHOT_PATTERN.lastIndex = 0;

  let match: RegExpExecArray | null = null;
  let snapshotText: string | null = null;

  while ((match = QUEUE_SNAPSHOT_PATTERN.exec(data)) !== null) {
    snapshotText = match[1];
  }

  if (!snapshotText) {
    return null;
  }

  try {
    return JSON.parse(snapshotText) as QueueSnapshot;
  } catch {
    return null;
  }
}