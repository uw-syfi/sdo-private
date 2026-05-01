import { stageIndex, extractStageSegments } from '../GraphAdapter';

describe('stageIndex', () => {
  it('extracts index from simple agent name', () => {
    expect(stageIndex('agent_0')).toBe(0);
  });

  it('extracts index from multi-word stage name', () => {
    expect(stageIndex('fan_out_1')).toBe(1);
    expect(stageIndex('judge_loop_2')).toBe(2);
    expect(stageIndex('summarize_3')).toBe(3);
  });

  it('returns -1 for names without numeric suffix', () => {
    expect(stageIndex('agent')).toBe(-1);
    expect(stageIndex('')).toBe(-1);
    expect(stageIndex('agent_abc')).toBe(-1);
  });
});

describe('extractStageSegments', () => {
  it('returns empty map when no TASK_START markers present', () => {
    const segments = extractStageSegments('some plain output\nno markers here');
    expect(segments.size).toBe(0);
  });

  it('creates one segment per TASK_START marker', () => {
    const data = [
      '__LEGO_TASK_START__ agent_0 uuid-a',
      'agent output line 1',
      'agent output line 2',
    ].join('\n');

    const segments = extractStageSegments(data);
    expect(segments.size).toBe(1);
    expect(segments.get(0)).toContain('agent output line 1');
    expect(segments.get(0)).toContain('agent output line 2');
  });

  it('segments multi-stage output at TASK_START boundaries', () => {
    const data = [
      '__LEGO_TASK_START__ agent_0 uuid-a',
      'stage 0 output',
      '__LEGO_TASK_START__ fan_out_1 uuid-b',
      'stage 1 output',
      '__LEGO_TASK_START__ summarize_2 uuid-c',
      'stage 2 output',
    ].join('\n');

    const segments = extractStageSegments(data);
    expect(segments.size).toBe(3);
    expect(segments.get(0)).toContain('stage 0 output');
    expect(segments.get(0)).not.toContain('stage 1 output');
    expect(segments.get(1)).toContain('stage 1 output');
    expect(segments.get(1)).not.toContain('stage 2 output');
    expect(segments.get(2)).toContain('stage 2 output');
  });

  it('handles judge_loop stage name correctly', () => {
    const data = '__LEGO_TASK_START__ judge_loop_0 uuid-x\nloop output';
    const segments = extractStageSegments(data);
    expect(segments.get(0)).toContain('loop output');
  });

  it('skips markers with non-numeric stage index', () => {
    const data = '__LEGO_TASK_START__ agent_abc uuid-x\nsome output';
    const segments = extractStageSegments(data);
    expect(segments.size).toBe(0);
  });

  it('correctly handles content before first TASK_START marker', () => {
    const data = [
      '__LEGO_STAGE_START__ agent_0',
      '__LEGO_TASK_START__ agent_0 uuid-a',
      'output after start',
    ].join('\n');

    const segments = extractStageSegments(data);
    expect(segments.size).toBe(1);
    expect(segments.get(0)).toContain('output after start');
    // Content before TASK_START (the STAGE_START line) is not in any segment
    expect(segments.get(0)).not.toContain('__LEGO_STAGE_START__');
  });
});
