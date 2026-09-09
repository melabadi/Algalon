import { readdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';

export interface SourceStats {
  files: number;
  lines: number;
  characters: number;
}

const sourceExtensions = new Set(['.css', '.html', '.js', '.jsx', '.svg', '.ts', '.tsx']);
const excludedDirectories = new Set(['.copilot-value', '.git', 'coverage', 'dist', 'node_modules']);
const excludedFiles = new Set(['package-lock.json', 'pnpm-lock.yaml', 'yarn.lock']);

async function sourceFiles(targetPath: string): Promise<string[]> {
  const targetStat = await stat(targetPath);
  if (targetStat.isFile()) {
    return sourceExtensions.has(path.extname(targetPath).toLowerCase()) && !excludedFiles.has(path.basename(targetPath))
      ? [targetPath]
      : [];
  }
  if (!targetStat.isDirectory()) return [];

  const files: string[] = [];
  for (const entry of await readdir(targetPath, { withFileTypes: true })) {
    if (entry.isSymbolicLink() || excludedDirectories.has(entry.name) || excludedFiles.has(entry.name)) continue;
    files.push(...await sourceFiles(path.join(targetPath, entry.name)));
  }
  return files;
}

export async function collectSourceStats(paths: string[]): Promise<SourceStats> {
  const files = [...new Set((await Promise.all(paths.map(sourceFiles))).flat())];
  let lines = 0;
  let characters = 0;
  for (const filePath of files) {
    const content = await readFile(filePath, 'utf8');
    characters += content.length;
    lines += content.length > 0 ? content.split(/\r?\n/).length : 0;
  }
  return { files: files.length, lines, characters };
}
