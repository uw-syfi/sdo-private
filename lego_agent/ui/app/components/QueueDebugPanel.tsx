import React from 'react';
import { Boxes, Inbox, Layers3 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { QueueSnapshot } from '@/lib/queueSnapshot';

interface QueueDebugPanelProps {
  snapshot: QueueSnapshot | null;
  className?: string;
}

export function QueueDebugPanel({ snapshot, className }: QueueDebugPanelProps) {
  return (
    <div className={cn('rounded-xl border border-slate-800 bg-slate-950/80 p-3 text-xs text-slate-300', className)}>
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2 text-slate-100 font-semibold uppercase tracking-[0.18em] text-[10px]">
          <Inbox className="w-3.5 h-3.5 text-emerald-400" />
          Queue Debug
        </div>
        <div className="flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-900 px-2 py-0.5 text-[10px] text-slate-400">
          <Layers3 className="w-3 h-3" />
          Cap {snapshot?.max_concurrent_workers ?? '...'}
        </div>
      </div>

      {!snapshot ? (
        <div className="rounded-lg border border-dashed border-slate-800 bg-slate-900/40 px-3 py-4 text-slate-500">
          Queue snapshots will appear here when a run starts.
        </div>
      ) : snapshot.stages.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-800 bg-slate-900/40 px-3 py-4 text-slate-500">
          No stages were reported by the runtime.
        </div>
      ) : (
        <div className="space-y-2">
          {snapshot.stages.map(stage => (
            <div key={stage.name} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-medium text-slate-100">{stage.name}</div>
                  <div className="mt-0.5 text-[10px] uppercase tracking-[0.18em] text-slate-500">
                    {stage.input_queue}{stage.output_queue ? ` → ${stage.output_queue}` : ''}
                  </div>
                </div>
                <div className="rounded-full border border-slate-700 bg-slate-950 px-2 py-0.5 text-[10px] text-slate-300">
                  {stage.active_total} active / {stage.total} waiting
                </div>
              </div>

              {(stage.active_tasks?.length ?? 0) > 0 && (
                <div className="mt-3">
                  <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-emerald-400/80">
                    In Flight
                  </div>
                  <div className="space-y-1.5">
                    {(stage.active_tasks ?? []).map(task => (
                      <div key={task.id} className="rounded-md border border-emerald-500/20 bg-emerald-500/5 px-2.5 py-2">
                        <div className="flex items-center gap-2 text-[11px] text-emerald-100">
                          <Boxes className="w-3 h-3 text-emerald-400 shrink-0" />
                          <span className="truncate">{task.label}</span>
                        </div>
                        <div className="mt-1 text-[10px] font-mono text-emerald-200/70 truncate">
                          {task.type} · {task.id}
                        </div>
                      </div>
                    ))}
                    {stage.active_truncated && (
                      <div className="text-[10px] text-slate-500 px-1">
                        +{stage.active_total - (stage.active_tasks?.length ?? 0)} more active tasks
                      </div>
                    )}
                  </div>
                </div>
              )}

              {stage.tasks.length > 0 ? (
                <div className="mt-3 space-y-1.5">
                  {stage.tasks.map(task => (
                    <div key={task.id} className="rounded-md border border-slate-800 bg-slate-950/80 px-2.5 py-2">
                      <div className="flex items-center gap-2 text-[11px] text-slate-200">
                        <Boxes className="w-3 h-3 text-emerald-400 shrink-0" />
                        <span className="truncate">{task.label}</span>
                      </div>
                      <div className="mt-1 text-[10px] font-mono text-slate-500 truncate">
                        {task.type} · {task.id}
                      </div>
                    </div>
                  ))}
                  {stage.truncated && (
                    <div className="text-[10px] text-slate-500 px-1">
                      +{stage.total - stage.tasks.length} more queued tasks
                    </div>
                  )}
                </div>
              ) : (
                <div className="mt-3 rounded-md border border-dashed border-slate-800 bg-slate-950/60 px-2.5 py-2 text-[11px] text-slate-500">
                  Queue empty.
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}