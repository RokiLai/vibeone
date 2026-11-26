    // Git Diff Modal
    function stripGitDiffPrefix(rawPath) {
      let p = String(rawPath || '').trim();
      if (p.startsWith('"') && p.endsWith('"') && p.length >= 2) {
        p = p.slice(1, -1);
      }
      if (p.startsWith('a/') || p.startsWith('b/')) {
        p = p.slice(2);
      }
      return p;
    }

    function parseDiffHeaderPath(headerLine) {
      const mQuoted = headerLine.match(/^diff --git "a\/(.+?)" "b\/(.+?)"$/);
      if (mQuoted) return mQuoted[2] || mQuoted[1];
      const mPlain = headerLine.match(/^diff --git a\/(.+?) b\/(.+)$/);
      if (mPlain) return mPlain[2] || mPlain[1];
      return headerLine.replace(/^diff --git\s+/, '').trim() || '未知文件';
    }

    function parseDiffFiles(diffText) {
      if (!diffText || !diffText.trim()) return [];
      const rawLines = diffText.split('\n');
      const files = [];
      let current = null;

      for (const line of rawLines) {
        if (line.startsWith('diff --git ')) {
          if (current) files.push(current);
          current = {
            path: parseDiffHeaderPath(line),
            status: 'M',
            renameFrom: '',
            renameTo: '',
            added: 0,
            deleted: 0,
            isBinary: false,
            lines: [line],
          };
          continue;
        }
        if (!current) {
          if (!line.trim()) continue;
          current = {
            path: 'Diff',
            status: 'M',
            renameFrom: '',
            renameTo: '',
            added: 0,
            deleted: 0,
            isBinary: false,
            lines: [],
          };
        }
        current.lines.push(line);
        if (line.startsWith('new file mode ')) {
          current.status = 'A';
        } else if (line.startsWith('deleted file mode ')) {
          current.status = 'D';
        } else if (line.startsWith('rename from ')) {
          current.status = 'R';
          current.renameFrom = line.slice('rename from '.length).trim();
        } else if (line.startsWith('rename to ')) {
          current.status = 'R';
          current.renameTo = line.slice('rename to '.length).trim();
        } else if (line.startsWith('Binary files ') || line === 'GIT binary patch') {
          current.isBinary = true;
        } else if (line.startsWith('+++ ')) {
          const p = line.slice(4).trim();
          if (p !== '/dev/null') current.path = stripGitDiffPrefix(p);
        } else if (line.startsWith('--- ')) {
          const p = line.slice(4).trim();
          if (current.status === 'D' && p !== '/dev/null') {
            current.path = stripGitDiffPrefix(p);
          }
        } else if (line.startsWith('+') && !line.startsWith('+++')) {
          current.added += 1;
        } else if (line.startsWith('-') && !line.startsWith('---')) {
          current.deleted += 1;
        }
      }
      if (current) files.push(current);

      for (const f of files) {
        if (f.status === 'R' && f.renameFrom && f.renameTo) {
          f.path = `${f.renameFrom} → ${f.renameTo}`;
        }
        while (f.lines.length && f.lines[f.lines.length - 1] === '') {
          f.lines.pop();
        }
      }
      return files;
    }

    function formatDiffLinesHtml(lines) {
      let oldLine = null;
      let newLine = null;
      return lines
        .map((line) => {
          let cls = 'text-slate-300';
          let oldNum = '';
          let newNum = '';

          if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff --git')) {
            cls = 'diff-line-file';
            oldLine = null;
            newLine = null;
          } else if (line.startsWith('@@')) {
            cls = 'diff-line-hunk';
            const m = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
            if (m) {
              oldLine = parseInt(m[1], 10);
              newLine = parseInt(m[2], 10);
            }
            oldNum = '···';
            newNum = '···';
          } else if (oldLine !== null && newLine !== null) {
            if (line.startsWith('+')) {
              cls = 'diff-line-add';
              newNum = String(newLine++);
            } else if (line.startsWith('-')) {
              cls = 'diff-line-del';
              oldNum = String(oldLine++);
            } else if (line.startsWith('\\')) {
              cls = 'text-slate-500';
            } else {
              oldNum = String(oldLine++);
              newNum = String(newLine++);
            }
          } else if (line.startsWith('+')) {
            cls = 'diff-line-add';
          } else if (line.startsWith('-')) {
            cls = 'diff-line-del';
          }

          return `<div class="diff-row ${cls}"><span class="diff-ln">${oldNum}</span><span class="diff-ln">${newNum}</span><span class="diff-code">${escapeHtml(line || ' ')}</span></div>`;
        })
        .join('');
    }

    const DIFF_BADGE_STYLES = {
      A: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/35',
      D: 'bg-rose-500/20 text-rose-300 border-rose-500/35',
      R: 'bg-amber-500/20 text-amber-300 border-amber-500/35',
      M: 'bg-blue-500/20 text-blue-300 border-blue-500/35',
    };

    const DIFF_EXPAND_ALL_LIMIT = 30;
    const DIFF_LOAD_CONCURRENCY = 4;

    const diffModalState = {
      workdir: '',
      profile: '',
      sessionId: '',
      requestSeq: 0,
    };

    function formatDiffHtml(diffText) {
      if (!diffText || !diffText.trim()) {
        return '<div class="rounded-xl border border-border bg-bg text-slate-400 py-6 text-center">暂无未提交代码改动</div>';
      }
      const files = parseDiffFiles(diffText);
      if (!files.length) {
        return '<div class="rounded-xl border border-border bg-bg text-slate-400 py-6 text-center">暂无未提交代码改动</div>';
      }
      return `<div class="space-y-2">${files
        .map((file) => {
          const badgeCls = DIFF_BADGE_STYLES[file.status] || DIFF_BADGE_STYLES.M;
          const statsHtml = file.isBinary
            ? '<span class="text-[10px] text-slate-400 shrink-0">Binary</span>'
            : `<span class="flex items-center gap-1.5 text-[11px] shrink-0">${
                file.added > 0 ? `<span class="text-emerald-400">+${file.added}</span>` : ''
              }${
                file.deleted > 0 ? `<span class="text-rose-400">-${file.deleted}</span>` : ''
              }</span>`;
          return `
            <details class="diff-file-card">
              <summary title="${escapeHtml(file.path)}">
                <div class="flex items-center gap-2 min-w-0 flex-1">
                  <svg class="diff-chevron w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5"/></svg>
                  <span class="text-[10px] font-semibold px-1.5 py-0.5 rounded border ${badgeCls} shrink-0">${escapeHtml(file.status)}</span>
                  <span class="text-xs text-slate-100 font-medium truncate">${escapeHtml(file.path)}</span>
                </div>
                ${statsHtml}
              </summary>
              <div class="diff-file-body">${formatDiffLinesHtml(file.lines)}</div>
            </details>
          `;
        })
        .join('')}</div>`;
    }

