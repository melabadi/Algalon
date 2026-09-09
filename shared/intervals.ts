export function unionIntervalDuration(
  intervals: Iterable<readonly [start: number, end: number]>
): number {
  const sorted = [...intervals]
    .filter(([start, end]) => end > start)
    .sort((left, right) => left[0] - right[0]);
  let total = 0;
  let currentStart: number | undefined;
  let currentEnd: number | undefined;
  for (const [start, end] of sorted) {
    if (currentStart === undefined || currentEnd === undefined) {
      currentStart = start;
      currentEnd = end;
    } else if (start <= currentEnd) {
      currentEnd = Math.max(currentEnd, end);
    } else {
      total += currentEnd - currentStart;
      currentStart = start;
      currentEnd = end;
    }
  }
  return currentStart === undefined || currentEnd === undefined
    ? total
    : total + currentEnd - currentStart;
}