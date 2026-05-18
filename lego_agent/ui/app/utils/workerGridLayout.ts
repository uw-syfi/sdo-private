export const DEFAULT_CELL_WIDTH = 200;
export const DEFAULT_CELL_HEIGHT = 72;
export const DEFAULT_GAP = 24;
export const DEFAULT_PADDING = 40;
export const DEFAULT_LABEL_INSET = 28;

export interface WorkerGridLayoutOptions {
  cellWidth?: number;
  cellHeight?: number;
  gap?: number;
  padding?: number;
  labelInset?: number;
}

export interface WorkerGridLayoutResult {
  positions: { x: number; y: number }[];
  bounds: { width: number; height: number };
  cols: number;
  rows: number;
}

/**
 * Compute a near-square grid layout for fan_out worker nodes.
 * Positions are relative to the parent group node.
 */
export function computeWorkerGridLayout(
  count: number,
  options: WorkerGridLayoutOptions = {},
): WorkerGridLayoutResult {
  const cellWidth = options.cellWidth ?? DEFAULT_CELL_WIDTH;
  const cellHeight = options.cellHeight ?? DEFAULT_CELL_HEIGHT;
  const gap = options.gap ?? DEFAULT_GAP;
  const padding = options.padding ?? DEFAULT_PADDING;
  const labelInset = options.labelInset ?? DEFAULT_LABEL_INSET;

  if (count <= 0) {
    return { positions: [], bounds: { width: 0, height: 0 }, cols: 0, rows: 0 };
  }

  const cols = Math.ceil(Math.sqrt(count));
  const rows = Math.ceil(count / cols);

  const positions: { x: number; y: number }[] = [];
  for (let i = 0; i < count; i++) {
    const col = i % cols;
    const row = Math.floor(i / cols);
    positions.push({
      x: padding + col * (cellWidth + gap),
      y: padding + labelInset + row * (cellHeight + gap),
    });
  }

  const width =
    padding * 2 + cols * cellWidth + Math.max(0, cols - 1) * gap;
  const height =
    padding * 2 + labelInset + rows * cellHeight + Math.max(0, rows - 1) * gap;

  return { positions, bounds: { width, height }, cols, rows };
}

export interface LayoutRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GroupContentBoundsOptions {
  padding?: number;
  labelInset?: number;
}

/**
 * Minimum width/height for a group node so all children fit with padding.
 * When children sit too close to the top, returns a topInset to shift them down
 * (room for the group label).
 */
export function computeGroupContentBounds(
  children: LayoutRect[],
  options: GroupContentBoundsOptions = {},
): { width: number; height: number; topInset: number } {
  const padding = options.padding ?? DEFAULT_PADDING;
  const labelInset = options.labelInset ?? DEFAULT_LABEL_INSET;

  if (children.length === 0) {
    return { width: 0, height: 0, topInset: 0 };
  }

  let maxRight = 0;
  let maxBottom = 0;
  let minY = Infinity;
  for (const child of children) {
    maxRight = Math.max(maxRight, child.x + child.width);
    maxBottom = Math.max(maxBottom, child.y + child.height);
    minY = Math.min(minY, child.y);
  }

  const topInset = minY < labelInset ? labelInset - minY : 0;

  return {
    width: maxRight + padding,
    height: maxBottom + padding + topInset,
    topInset,
  };
}
