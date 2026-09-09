import { createReadStream } from 'node:fs';
import { readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { createInterface } from 'node:readline';

export interface TraceArchiveSegment {
  path: string;
  modifiedNanoseconds: bigint;
  size: number;
  identity?: string;
}

export interface TraceArchiveCursorEntry {
  offset: number;
  size: number;
  modifiedNanoseconds: string;
}

export interface TraceArchiveCursor {
  segments: Record<string, TraceArchiveCursorEntry>;
}

export interface IncrementalJsonLines<T> {
  records: T[];
  cursor: TraceArchiveCursor;
  bytesRead: number;
}

function sameSnapshot(
  left: readonly TraceArchiveSegment[],
  right: readonly TraceArchiveSegment[]
): boolean {
  return left.length === right.length && left.every((segment, index) => {
    const candidate = right[index];
    return candidate !== undefined &&
      segment.path === candidate.path &&
      segment.modifiedNanoseconds === candidate.modifiedNanoseconds &&
      segment.size === candidate.size &&
      segment.identity === candidate.identity;
  });
}

export async function readStableTraceArchive<T>(
  snapshot: () => Promise<TraceArchiveSegment[]>,
  read: (segments: readonly TraceArchiveSegment[]) => Promise<T>,
  maxAttempts = 3
): Promise<T> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const before = await snapshot();
    const result = await read(before);
    const after = await snapshot();
    if (sameSnapshot(before, after)) return result;
  }
  throw new Error(`Trace archive changed during ${maxAttempts} consecutive reads.`);
}

async function traceArchiveSnapshot(filePath: string): Promise<TraceArchiveSegment[]> {
  const parsed = path.parse(filePath);
  const directory = parsed.dir || '.';
  const rotatedPrefix = `${parsed.name}-`;
  const entries = await readdir(directory, { withFileTypes: true });
  const rotatedPaths = entries
    .filter((entry) => entry.isFile() && entry.name.startsWith(rotatedPrefix) && entry.name.endsWith(parsed.ext))
    .map((entry) => path.join(directory, entry.name))
    .sort((left, right) => left.localeCompare(right));
  const segments: TraceArchiveSegment[] = [];
  for (const archivePath of [...rotatedPaths, filePath]) {
    try {
      const archiveStat = await stat(archivePath, { bigint: true });
      segments.push({
        path: archivePath,
        modifiedNanoseconds: archiveStat.mtimeNs,
        size: Number(archiveStat.size),
        identity: `${archiveStat.dev}:${archiveStat.ino}`
      });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
  }
  return segments;
}

export async function readStableJsonLines<T>(
  filePath: string,
  createResult: () => T,
  consume: (result: T, record: unknown) => void
): Promise<T> {
  return readStableTraceArchive(traceArchiveSnapshot.bind(null, filePath), async (segments) => {
    const result = createResult();
    for (const segment of segments) {
      const input = createReadStream(segment.path, { encoding: 'utf8' });
      const lines = createInterface({ input, crlfDelay: Infinity });
      let pendingLine: string | undefined;
      try {
        for await (const line of lines) {
          if (pendingLine?.trim()) consume(result, JSON.parse(pendingLine));
          pendingLine = line;
        }
        if (pendingLine?.trim()) {
          try {
            consume(result, JSON.parse(pendingLine));
          } catch {
          }
        }
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      } finally {
        lines.close();
        input.destroy();
      }
    }
    return result;
  });
}

async function readJsonLinesFromOffset<T>(
  segment: TraceArchiveSegment,
  startOffset: number
): Promise<{ records: T[]; offset: number }> {
  if (startOffset >= segment.size) return { records: [], offset: startOffset };
  const input = createReadStream(segment.path, {
    start: startOffset,
    end: segment.size - 1
  });
  const records: T[] = [];
  let pending = Buffer.alloc(0);
  let consumedOffset = startOffset;
  try {
    for await (const chunk of input) {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      pending = pending.length === 0 ? bytes : Buffer.concat([pending, bytes]);
      let newline = pending.indexOf(0x0a);
      while (newline >= 0) {
        const line = pending.subarray(0, newline).toString('utf8').replace(/\r$/, '');
        consumedOffset += newline + 1;
        pending = pending.subarray(newline + 1);
        if (line.trim()) records.push(JSON.parse(line) as T);
        newline = pending.indexOf(0x0a);
      }
    }
    if (pending.toString('utf8').trim()) {
      try {
        records.push(JSON.parse(pending.toString('utf8')) as T);
        consumedOffset = segment.size;
      } catch {
      }
    } else {
      consumedOffset = segment.size;
    }
  } finally {
    input.destroy();
  }
  return { records, offset: consumedOffset };
}

export async function readIncrementalJsonLines<T>(
  filePath: string,
  cursor: TraceArchiveCursor = { segments: {} }
): Promise<IncrementalJsonLines<T>> {
  return readStableTraceArchive(traceArchiveSnapshot.bind(null, filePath), async (segments) => {
    const records: T[] = [];
    const nextCursor: TraceArchiveCursor = { segments: {} };
    let bytesRead = 0;
    for (const segment of segments) {
      const identity = segment.identity ?? segment.path;
      const previous = cursor.segments[identity];
      const startOffset = previous && segment.size >= previous.offset
        ? previous.offset
        : 0;
      const result = await readJsonLinesFromOffset<T>(segment, startOffset);
      records.push(...result.records);
      bytesRead += Math.max(0, segment.size - startOffset);
      nextCursor.segments[identity] = {
        offset: result.offset,
        size: segment.size,
        modifiedNanoseconds: String(segment.modifiedNanoseconds)
      };
    }
    return { records, cursor: nextCursor, bytesRead };
  });
}

export function readTraceRecords<T>(filePath: string): Promise<T[]> {
  return readStableJsonLines<T[]>(filePath, () => [], (records, record) => {
    records.push(record as T);
  });
}