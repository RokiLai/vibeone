    // =========================================================================
    // Lightweight Bilingual i18n (zh / en) — Zero Dependencies
    // =========================================================================
    (function initVibeOneI18n() {
      const STORAGE_KEY = 'vibeone_lang';
      const ATTRS = ['placeholder', 'title', 'aria-label'];
      const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'PRE', 'TEXTAREA', 'SVG']);

      const ZH_TO_EN = window.__VIBEONE_ZH_TO_EN || {};

      const ZH_PATTERNS = [
        [/^账号:\s*(.+)$/, (_, p) => `Account: ${p}`],
        [/^账号登录 · (.+)$/, (_, p) => `Account Sign-In · ${p}`],
        [/^已切换账号:\s*(.+)$/, (_, p) => `Switched to account: ${p}`],
        [/^首次启动账号 (.+)，请稍候\.\.\.$/, (_, n) => `Starting account ${n} for the first time...`],
        [/^正在热重载账号 (.+)\.\.\.$/, (_, n) => `Hot-reloading account ${n}...`],
        [/^账号 (.+) 热重载成功$/, (_, n) => `Account ${n} hot-reloaded`],
        [/^账号 (.+) 登录成功$/, (_, n) => `Account ${n} signed in`],
        [/^账号 (.+) 有任务正在运行，确定强制重载吗？$/, (_, n) => `Account ${n} has running tasks. Force reload anyway?`],
        [/^上下文:\s*(.+)$/, (_, rest) => `Context: ${rest}`],
        [/^当前目录:\s*(.+)$/, (_, d) => `Workspace: ${d}`],
        [/^目录「(.+)」$/, (_, d) => `Folder "${d}"`],
        [/^在 (.+) 新建会话$/, (_, d) => `New session in ${d}`],
        [/^正在 (.+) 创建新会话\.\.\.$/, (_, d) => `Creating session in ${d}...`],
        [/^＋ 在「(.+)」创建新会话$/, (_, d) => `+ New Session in "${d}"`],
        [/^在 (.+) 下新建文件夹名称：$/, (_, p) => `New folder name inside ${p}:`],
        [/^已创建目录:\s*(.+)$/, (_, n) => `Folder created: ${n}`],
        [/^从侧边栏移除目录 (.+)$/, (_, d) => `Remove folder ${d} from sidebar`],
        [/^批量归档 (.+) 下的会话$/, (_, d) => `Batch archive sessions in ${d}`],
        [/^恢复 (.+) 下全部已归档会话$/, (_, d) => `Restore all archived sessions in ${d}`],
        [/^已移除会话「(.+)」$/, (_, t) => `Removed session "${t}"`],
        [/^已移除目录「(.+)」$/, (_, d) => `Removed folder "${d}"`],
        [/^已恢复会话「(.+)」$/, (_, l) => `Restored session "${l}"`],
        [/^已恢复 (\d+) 条会话$/, (_, n) => `Restored ${n} session(s)`],
        [/^已归档 (\d+) 条会话，可在「设置 → 已归档会话」中恢复$/, (_, n) => `Archived ${n} session(s). Restore anytime in Settings → Archived.`],
        [/^正在加载「(.+)」\.\.\.$/, (_, t) => `Loading "${t}"...`],
        [/^确定从侧边栏移除会话「(.+)」吗？$/, (_, t) => `Remove session "${t}" from the sidebar?`],
        [/^确定从侧边栏移除目录「(.+)」(及其下 (\d+) 条会话记录)?吗？\n（仅从列表移除，不会删除本地磁盘文件）$/, (_, d, _h, c) => `Remove folder "${d}"${c ? ` and its ${c} session(s)` : ''} from the sidebar?\n(Only hides from the list — local files are never deleted.)`],
        [/^确定归档「(.+)」下的 (\d+) 条会话吗？(（包含当前会话，将自动切换到其他会话）)?\n（记录会完整保留，随时可在「设置 → 已归档会话」中查看或恢复）$/, (_, d, n, cur) => `Archive ${n} session(s) in "${d}"?${cur ? ' (Includes active session; will switch automatically.)' : ''}\n(History is preserved and can be restored in Settings → Archived.)`],
        [/^已选 (\d+) \/ (\d+)$/, (_, a, b) => `${a} / ${b} selected`],
        [/^归档 \((\d+)\)$/, (_, n) => `Archive (${n})`],
        [/^7天前\((\d+)\)$/, (_, n) => `>7d (${n})`],
        [/^(\d+)分前$/, (_, n) => `${n}m ago`],
        [/^(\d+)时前$/, (_, n) => `${n}h ago`],
        [/^(\d+)天前$/, (_, n) => `${n}d ago`],
        [/^(\d+)分钟$/, (_, n) => `${n}m`],
        [/^(\d+)小时(\d+)分$/, (_, h, m) => `${h}h ${m}m`],
        [/^(\d+)天(\d+)小时$/, (_, d, h) => `${d}d ${h}h`],
        [/^(\d+)天$/, (_, d) => `${d}d`],
        [/^(.+) 重置（(.+)后）$/, (_, t, rel) => `Resets ${t} (in ${rel.replace(/(\d+)天(\d+)小时/, '$1d $2h').replace(/(\d+)天/, '$1d').replace(/(\d+)小时(\d+)分/, '$1h $2m').replace(/(\d+)分钟/, '$1m')})`],
        [/^(\d+) 条$/, (_, n) => `${n} items`],
        [/^(\d+) 档$/, (_, n) => `${n} levels`],
        [/^(\d+) 行$/, (_, n) => `${n} lines`],
        [/^· (\d+) 个文件$/, (_, n) => `· ${n} files`],
        [/^\((\d+)处\)$/, (_, n) => `(${n})`],
        [/^\+(\d+)图$/, (_, n) => `+${n} img`],
        [/^\[(\d+) 张图片\]$/, (_, n) => `[${n} image(s)]`],
        [/^(\d+) 待确认 · (\d+) 运行中$/, (_, p, r) => `${p} pending · ${r} running`],
        [/^(\d+) 待确认$/, (_, n) => `${n} pending`],
        [/^(\d+) 运行中$/, (_, n) => `${n} running`],
        [/^Codex (.+) 额度$/, (_, p) => `Codex ${p} Quota`],
        [/^Credits 余额:\s*(.+)$/, (_, b) => `Credits balance: ${b}`],
        [/^免费额度重置次数:\s*(\d+) 次$/, (_, n) => `Free quota resets: ${n}`],
        [/^调用 Codex 技能 (.+)$/, (_, n) => `Run Codex skill ${n}`],
        [/^编辑 (.+) 等 (\d+) 个文件$/, (_, f, n) => `Edit ${f} and ${n} files`],
        [/^编辑 (.+)$/, (_, f) => `Edit ${f}`],
        [/^搜索:\s*(.+)$/, (_, q) => `Search: ${q}`],
        [/^查看图片 (.+)$/, (_, p) => `View image ${p}`],
        [/^已分叉出新会话：(.+)$/, (_, t) => `Forked new session: ${t}`],
        [/^(.+) \(分支\)$/, (_, t) => `${t} (Fork)`],
        [/^后台会话「(.+)」已完成$/, (_, l) => `Background session "${l}" completed`],
        [/^后台会话「(.+)」执行出错$/, (_, l) => `Background session "${l}" failed`],
        [/^后台会话「(.+)」响应提前中断$/, (_, l) => `Background session "${l}" interrupted`],
        [/^响应提前中断：(.+)，可点击「继续」接续执行。$/, (_, r) => `Response interrupted: ${r}. Tap "Resume" to continue.`],
        [/^账号 (.+) 尚未登录，请先在终端登录该账号$/, (_, p) => `Account ${p} is not signed in. Please sign in via terminal first.`],
        [/^当前账号有 (\d+) 个任务正在运行，切换账号将中断它们$/, (_, n) => `Current account has ${n} running task(s); switching will interrupt them.`],
        [/^账号 (.+) 当前有 (\d+) 个任务正在运行，重载将中断它们$/, (_, p, n) => `Account ${p} has ${n} running task(s); reloading will interrupt them.`],
        [/^账号名 (.+) 已被 Codex 账号占用$/, (_, p) => `Profile name ${p} is already used by a Codex account`],
        [/^账号名 (.+) 已被 AGY 账号占用$/, (_, p) => `Profile name ${p} is already used by an AGY account`],
        [/^Codex 账号配置目录不存在:\s*(.+)$/, (_, p) => `Codex profile directory not found: ${p}`],
        [/^登录成功，正在热重载账号 (.+)\.\.\.$/, (_, p) => `Signed in! Hot-reloading account ${p}...`],
        [/^账号 (.+) 登录并热重载成功（(.+)）$/, (_, p, e) => `Account ${p} signed in & reloaded (${e})`],
        [/^账号 (.+) 登录并热重载成功$/, (_, p) => `Account ${p} signed in & reloaded`],
        [/^登录凭证已保存，但自动重载失败（(.+)），可点击手动重载$/, (_, e) => `Credentials saved, but auto-reload failed (${e}). Tap Reload manually.`],
        [/^Codex 登录失败:\s*(.+)$/, (_, e) => `Codex sign-in failed: ${e}`],
        [/^AGY 登录失败:\s*(.+)$/, (_, e) => `AGY sign-in failed: ${e}`],
        [/^提交授权码失败:\s*(.+)$/, (_, e) => `Failed to submit auth code: ${e}`],
        [/^当前登录状态不可提交（(.+)）$/, (_, s) => `Cannot submit in current state (${s})`],
        [/^(\d+) 个会话待确认$/, (_, n) => `${n} session(s) awaiting approval`],
        [/^(\d+) 个会话运行中$/, (_, n) => `${n} session(s) running`],
        [/^后台待确认 (\d+)$/, (_, n) => `${n} bg approval`],
        [/^后台 (\d+) 运行中$/, (_, n) => `${n} bg running`],
        [/^(\d+) 步思考$/, (_, n) => `${n} thoughts`],
        [/^(\d+) 次工具$/, (_, n) => `${n} tools`],
        [/^思考过程 · (\d+) 步$/, (_, n) => `Thinking · ${n} step(s)`],
        [/^工具执行 · (\d+) 次$/, (_, n) => `Tools · ${n} call(s)`],
        [/^执行过程 · (.+)$/, (_, s) => `Steps · ${s.replace(/(\d+) 步思考/g, '$1 thoughts').replace(/(\d+) 次工具/g, '$1 tools')}`],
        [/^正在执行 · (.+)$/, (_, s) => `Running · ${s.replace(/(\d+) 步思考/g, '$1 thoughts').replace(/(\d+) 次工具/g, '$1 tools')}`],
        [/^待发送队列 \((\d+)\) · 已暂停$/, (_, n) => `Queue (${n}) · Paused`],
        [/^待发送队列 \((\d+)\) · 当前回合结束后自动执行$/, (_, n) => `Queue (${n}) · Runs after current turn`],
        [/^Passkey 已绑定 \((\d+)\) · 新增$/, (_, n) => `Passkeys (${n}) · Add`],
        [/^加载更早记录（还有 (\d+) 条）$/, (_, n) => `Load older (${n} more)`],
        [/^已同步 (\d+) 条最新终端记录$/, (_, n) => `Synced ${n} new CLI item(s)`],
        [/^已切换执行模式:\s*(.+)$/, (_, m) => `Switched mode to: ${m}`],
        [/^\/help · (.+) 可用指令$/, (_, e) => `/help · ${e} Commands`],
        [/^预览文件:\s*(.+)$/, (_, p) => `Preview: ${p}`],
        [/^文件较大（共 ([^）]+)），仅预览前 ([^）]+) 内容。$/, (_, s, sh) => `Large file (${s} total), showing first ${sh}.`],
        [/^无法读取文件 \(HTTP (\d+)\)$/, (_, c) => `Cannot read file (HTTP ${c})`],
        [/^加载失败 \((\d+)\)$/, (_, c) => `Load failed (${c})`],
        [/^服务器暂时不可用（HTTP (\d+)），请稍后重试。$/, (_, c) => `Server temporarily unavailable (HTTP ${c}). Please retry.`],
        [/^开启推送失败:\s*(.+)$/, (_, m) => `Failed to enable push: ${m === '请确认使用 HTTPS 访问' ? 'please ensure HTTPS access' : m}`],
        [/^已复制设备码：(.+)$/, (_, c) => `Device code copied: ${c}`],
        [/^「引用」(.+)$/, (_, b) => `[Quote] ${b}`],
        [/^错误:\s*(.*)$/, (_, e) => `Error: ${e}`],
      ];

      function detectInitialLang() {
        try {
          const saved = localStorage.getItem(STORAGE_KEY);
          if (saved === 'en' || saved === 'zh') return saved;
        } catch (_) {}
        const nav = (navigator.language || navigator.userLanguage || '').toLowerCase();
        return nav.startsWith('zh') ? 'zh' : 'en';
      }

      let currentLang = detectInitialLang();
      let _applying = false;
      const origTextMap = new WeakMap();
      const origAttrMap = new WeakMap();

      function translateText(raw) {
        if (currentLang !== 'en' || typeof raw !== 'string' || !raw) return raw;
        if (!/[\u4e00-\u9fff]/.test(raw)) return raw;
        const m = raw.match(/^(\s*)([\s\S]*?)(\s*)$/);
        if (!m || !m[2]) return raw;
        const [, lead, core, trail] = m;
        if (Object.prototype.hasOwnProperty.call(ZH_TO_EN, core)) {
          return lead + ZH_TO_EN[core] + trail;
        }
        for (let i = 0; i < ZH_PATTERNS.length; i++) {
          const [re, fn] = ZH_PATTERNS[i];
          if (re.test(core)) {
            return lead + core.replace(re, fn) + trail;
          }
        }
        return raw;
      }

      function shouldSkipElement(el) {
        if (!el || el.nodeType !== 1) return false;
        if (SKIP_TAGS.has(el.tagName)) return true;
        if (el.getAttribute('data-i18n-skip') === '1') return true;
        if (el.classList && (el.classList.contains('markdown-body') || el.classList.contains('user-msg-text'))) {
          return true;
        }
        return false;
      }

      function processTextNode(node) {
        const val = node.nodeValue;
        if (!val || !val.trim()) return;
        let orig = origTextMap.get(node);
        if (orig === undefined) {
          if (/[\u4e00-\u9fff]/.test(val)) {
            orig = val;
            origTextMap.set(node, orig);
          } else {
            return;
          }
        } else if (currentLang === 'en' && /[\u4e00-\u9fff]/.test(val)) {
          // Node was dynamically updated with fresh Chinese text
          orig = val;
          origTextMap.set(node, orig);
        }
        const target = currentLang === 'en' ? translateText(orig) : orig;
        if (node.nodeValue !== target) {
          node.nodeValue = target;
        }
      }

      function processElementAttrs(el) {
        let store = origAttrMap.get(el);
        for (let i = 0; i < ATTRS.length; i++) {
          const attr = ATTRS[i];
          const val = el.getAttribute(attr);
          if (!val) continue;
          let orig = store ? store[attr] : undefined;
          if (orig === undefined) {
            if (/[\u4e00-\u9fff]/.test(val)) {
              if (!store) {
                store = {};
                origAttrMap.set(el, store);
              }
              orig = val;
              store[attr] = orig;
            } else {
              continue;
            }
          } else if (currentLang === 'en' && /[\u4e00-\u9fff]/.test(val)) {
            store[attr] = val;
            orig = val;
          }
          const target = currentLang === 'en' ? translateText(orig) : orig;
          if (val !== target) {
            el.setAttribute(attr, target);
          }
        }
      }

      function applyI18nToTree(root) {
        if (!root) return;
        _applying = true;
        try {
          const walk = (node) => {
            if (!node) return;
            if (node.nodeType === 3) {
              processTextNode(node);
              return;
            }
            if (node.nodeType === 1) {
              if (shouldSkipElement(node)) return;
              processElementAttrs(node);
              for (let child = node.firstChild; child; child = child.nextSibling) {
                walk(child);
              }
            }
          };
          walk(root);
        } finally {
          _applying = false;
        }
      }

      function syncLangButtons() {
        document.documentElement.lang = currentLang === 'en' ? 'en' : 'zh-CN';
        document.querySelectorAll('[data-lang-choice]').forEach((btn) => {
          const active = btn.getAttribute('data-lang-choice') === currentLang;
          btn.classList.toggle('bg-blue-600', active);
          btn.classList.toggle('text-white', active);
          btn.classList.toggle('border-blue-500/60', active);
          btn.classList.toggle('bg-bg', !active);
          btn.classList.toggle('text-slate-300', !active);
          btn.classList.toggle('border-border', !active);
        });
      }

      function setLang(lang) {
        const next = lang === 'en' ? 'en' : 'zh';
        currentLang = next;
        try {
          localStorage.setItem(STORAGE_KEY, next);
        } catch (_) {}
        syncLangButtons();
        applyI18nToTree(document.body);
      }

      // Wrap native confirm/prompt dialogs so dynamic prompts are translated too
      const _origConfirm = window.confirm.bind(window);
      window.confirm = (msg) => _origConfirm(translateText(String(msg ?? '')));
      const _origPrompt = window.prompt.bind(window);
      window.prompt = (msg, def) => _origPrompt(translateText(String(msg ?? '')), def);

      // Expose global helpers
      window.getVibeOneLang = () => currentLang;
      window.setVibeOneLang = setLang;
      window.tVibeOne = translateText;

      // Bind click handlers for any [data-lang-choice] button
      document.addEventListener('click', (e) => {
        const btn = e.target && e.target.closest ? e.target.closest('[data-lang-choice]') : null;
        if (!btn) return;
        const choice = btn.getAttribute('data-lang-choice');
        if (choice === 'zh' || choice === 'en') {
          setLang(choice);
        }
      });

      // Observe dynamic DOM updates from app-*.js
      const observer = new MutationObserver((mutations) => {
        if (_applying || currentLang !== 'en') return;
        _applying = true;
        try {
          for (let i = 0; i < mutations.length; i++) {
            const m = mutations[i];
            if (m.type === 'characterData') {
              const p = m.target.parentElement;
              if (p && !p.closest('pre, script, style, textarea, .markdown-body, .user-msg-text, [data-i18n-skip="1"]')) {
                processTextNode(m.target);
              }
            } else if (m.type === 'attributes') {
              if (!shouldSkipElement(m.target)) {
                processElementAttrs(m.target);
              }
            } else if (m.type === 'childList') {
              for (let j = 0; j < m.addedNodes.length; j++) {
                const n = m.addedNodes[j];
                const parentEl = n.nodeType === 1 ? n : n.parentElement;
                if (parentEl && parentEl.closest && parentEl.closest('pre, script, style, textarea, .markdown-body, .user-msg-text, [data-i18n-skip="1"]')) {
                  continue;
                }
                const walk = (node) => {
                  if (!node) return;
                  if (node.nodeType === 3) processTextNode(node);
                  else if (node.nodeType === 1 && !shouldSkipElement(node)) {
                    processElementAttrs(node);
                    for (let c = node.firstChild; c; c = c.nextSibling) walk(c);
                  }
                };
                walk(n);
              }
            }
          }
        } finally {
          _applying = false;
        }
      });

      function startI18n() {
        syncLangButtons();
        if (currentLang === 'en') {
          applyI18nToTree(document.body);
        }
        observer.observe(document.body, {
          childList: true,
          subtree: true,
          characterData: true,
          attributes: true,
          attributeFilter: ATTRS,
        });
      }

      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startI18n, { once: true });
      } else {
        startI18n();
      }
    })();
