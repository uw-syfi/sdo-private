import {
  computeGroupContentBounds,
  computeWorkerGridLayout,
  DEFAULT_CELL_HEIGHT,
  DEFAULT_CELL_WIDTH,
  DEFAULT_GAP,
  DEFAULT_LABEL_INSET,
  DEFAULT_PADDING,
} from '../workerGridLayout';

describe('computeWorkerGridLayout', () => {
  it('returns empty result for count 0', () => {
    const result = computeWorkerGridLayout(0);
    expect(result.positions).toEqual([]);
    expect(result.bounds).toEqual({ width: 0, height: 0 });
    expect(result.cols).toBe(0);
    expect(result.rows).toBe(0);
  });

  it('returns a single position for count 1', () => {
    const result = computeWorkerGridLayout(1);
    expect(result.positions).toHaveLength(1);
    expect(result.cols).toBe(1);
    expect(result.rows).toBe(1);
    expect(result.positions[0]).toEqual({
      x: DEFAULT_PADDING,
      y: DEFAULT_PADDING + DEFAULT_LABEL_INSET,
    });
  });

  it('returns two unique positions in one row for count 2', () => {
    const result = computeWorkerGridLayout(2);
    expect(result.positions).toHaveLength(2);
    expect(result.cols).toBe(2);
    expect(result.rows).toBe(1);
    expect(result.positions[0]).not.toEqual(result.positions[1]);
  });

  it('uses 4 columns for 16 workers', () => {
    const result = computeWorkerGridLayout(16);
    expect(result.cols).toBe(4);
    expect(result.rows).toBe(4);
    expect(result.positions).toHaveLength(16);
  });

  it('assigns unique positions for 16 workers', () => {
    const { positions } = computeWorkerGridLayout(16);
    const keys = positions.map((p) => `${p.x},${p.y}`);
    expect(new Set(keys).size).toBe(16);
  });

  it('bounds contain the rightmost and bottommost cell', () => {
    const { positions, bounds } = computeWorkerGridLayout(16);
    const last = positions[15];
    expect(bounds.width).toBeGreaterThanOrEqual(
      last.x + DEFAULT_CELL_WIDTH + DEFAULT_PADDING,
    );
    expect(bounds.height).toBeGreaterThanOrEqual(
      last.y + DEFAULT_CELL_HEIGHT + DEFAULT_PADDING,
    );
  });

  it('respects custom cell dimensions', () => {
    const { positions } = computeWorkerGridLayout(2, {
      cellWidth: 100,
      gap: 10,
    });
    expect(positions[1].x - positions[0].x).toBe(100 + 10);
  });
});

describe('computeGroupContentBounds', () => {
  it('returns zero bounds for no children', () => {
    expect(computeGroupContentBounds([])).toEqual({
      width: 0,
      height: 0,
      topInset: 0,
    });
  });

  it('adds padding around the child bounding box', () => {
    const bounds = computeGroupContentBounds([
      { x: DEFAULT_PADDING, y: DEFAULT_PADDING + DEFAULT_LABEL_INSET, width: 200, height: 72 },
    ]);
    expect(bounds.width).toBe(DEFAULT_PADDING + 200 + DEFAULT_PADDING);
    expect(bounds.height).toBe(
      DEFAULT_PADDING + DEFAULT_LABEL_INSET + 72 + DEFAULT_PADDING,
    );
    expect(bounds.topInset).toBe(0);
  });

  it('requests topInset when children start above the label band', () => {
    const bounds = computeGroupContentBounds([
      { x: 10, y: 5, width: 100, height: 50 },
    ]);
    expect(bounds.topInset).toBe(DEFAULT_LABEL_INSET - 5);
    expect(bounds.height).toBeGreaterThanOrEqual(5 + 50 + DEFAULT_PADDING + bounds.topInset);
  });
});
