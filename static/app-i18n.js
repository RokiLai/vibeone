    // =========================================================================
    // Lightweight Bilingual i18n (zh / en) — Zero Dependencies
    // =========================================================================
    (function initVibeOneI18n() {
      const STORAGE_KEY = 'vibeone_lang';
      const ATTRS = ['placeholder', 'title', 'aria-label'];
      const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'PRE', 'TEXTAREA', 'SVG']);

      const ZH_TO_EN = {
        // Offline & Auth
        '无法连接服务器': 'Cannot reach server',
        '当前处于离线状态或服务器暂时不可达，恢复网络后将自动重连。': 'You are offline or the server is unreachable. Reconnecting automatically once back online.',
        '当前设备已断网，恢复网络连接后将自动重连。': 'Device is offline. Will reconnect automatically when back online.',
        '无法连接到 VibeOne 服务器，请检查网络或服务状态后重试。': 'Cannot connect to VibeOne server. Check your network or server status and retry.',
        '重新连接': 'Reconnect',
        '正在连接...': 'Connecting...',
        '请输入访问密钥 (VIBEONE_WEB_TOKEN)': 'Enter access token (VIBEONE_WEB_TOKEN)',
        '输入访问密码...': 'Enter access token...',
        '解锁并连接': 'Unlock & Connect',
        '使用 Passkey / 面容解锁': 'Unlock with Passkey / Face ID',
        '密钥错误，请重试': 'Invalid token, please try again',
        '网络错误，无法连接服务器': 'Network error, cannot reach server',
        '网络异常': 'Network error',
        '登录已过期': 'Session expired',
        '尚未绑定 Passkey，请先用密钥解锁': 'No Passkey bound yet. Unlock with your token first.',
        'Passkey 解锁已取消或失败': 'Passkey unlock cancelled or failed',
        'Passkey 验证失败，请重试或使用密钥解锁': 'Passkey verification failed. Retry or unlock with token.',
        'Passkey 绑定成功，下次可直接面容/钥匙串解锁': 'Passkey bound! You can now unlock with Face ID / Keychain.',
        'Passkey 绑定失败': 'Failed to bind Passkey',
        'Passkey 绑定失败，请重试': 'Failed to bind Passkey, please try again',
        '当前环境不支持 Passkey（需 HTTPS 或 localhost）': 'Passkey requires HTTPS or localhost',
        '当前设备已绑定过此 Passkey': 'This device already has a bound Passkey',
        '获取 Passkey 注册参数失败': 'Failed to fetch Passkey registration options',

        // Sidebar & Header
        '加载中...': 'Loading...',
        '未命名会话': 'Untitled Session',
        '预览图片': 'Image Preview',
        '连接状态': 'Connection status',
        '刷新会话列表': 'Refresh sessions',
        '已刷新会话列表': 'Sessions refreshed',
        '关闭侧边栏': 'Close sidebar',
        '切换 Antigravity 账号': 'Switch account',
        '账号: 加载中...': 'Account: Loading...',
        '切换': 'Switch',
        '切换中...': 'Switching...',
        '重载中...': 'Reloading...',
        '当前': 'Active',
        '当前使用': 'In Use',
        '去登录': 'Sign in',
        '已登录': 'Signed in',
        '未登录（点击登录）': 'Not signed in (tap to login)',
        '未检测到可用账号配置': 'No configured accounts found',
        '单账号热重载（不重启服务）': 'Hot-reload this account (no restart)',
        '新增 AGY': 'Add AGY',
        '新增 Codex': 'Add Codex',
        '新增 AGY 账号': 'Add AGY Account',
        '新增 Codex 账号': 'Add Codex Account',
        '新建会话': 'New Session',
        '新会话': 'New Session',
        '在当前项目新建会话': 'New session in current project',
        '选择工作目录新建会话': 'Choose workspace for new session',
        '搜索当前账号的项目、会话或消息全文...': 'Search projects, sessions, or messages...',
        '搜索当前账号的已归档会话或消息全文...': 'Search archived sessions or messages...',
        '正在当前账号中全文搜索...': 'Searching current account...',
        '未找到匹配的项目、会话或消息': 'No matching projects, sessions, or messages',
        '未找到匹配的已归档会话': 'No matching archived sessions',
        '暂无历史会话': 'No sessions yet',
        '暂无已归档会话': 'No archived sessions',
        '暂无会话，点击右侧 + 新建': 'No sessions yet — tap + to create one',
        '正在加载工作区与会话...': 'Loading workspaces & sessions...',
        '正在创建新会话...': 'Creating new session...',
        '运行中': 'Running',
        '待确认': 'Approval',
        '运行中任务': 'Active Tasks',
        '打开会话与工作区列表': 'Open sessions & workspaces',
        '打开项目与历史会话': 'Open projects & sessions',
        '当前工作目录': 'Current workspace',
        '查看当前目录 Git Diff': 'View Git Diff',
        '查看当前工作目录的 Git Diff 代码改动': 'View Git Diff for current workspace',
        '查看后台运行中的会话': 'View background running sessions',
        '上下文: -': 'Context: -',
        '设置': 'Settings',
        '打开设置': 'Open Settings',
        '返回设置': 'Back to Settings',
        '刚刚': 'Just now',
        '全选': 'Select All',
        '仅留3条': 'Keep 3',
        '退出批量归档': 'Exit batch archive',
        '取消': 'Cancel',
        '关闭': 'Close',
        '刷新': 'Refresh',
        '清空': 'Clear',
        '继续': 'Resume',
        '继续执行': 'Resume',
        '允许': 'Allow',
        '允许一次': 'Allow Once',
        '允许本次': 'Allow Once',
        '本会话允许': 'Allow for Session',
        '确认': 'Confirm',
        '拒绝': 'Deny',
        '执行命令审批': 'Command Approval',
        '文件修改审批': 'File Edit Approval',
        '权限提升审批': 'Permission Escalation',
        '操作权限': 'Permission Required',
        'Codex 请求输入': 'Codex Input Request',
        'auto (自动执行)': 'auto (Auto)',
        'full-access (完全权限)': 'full-access (Full Access)',
        'on-request (按需审批)': 'on-request (On Request)',
        'read-only (只读)': 'read-only (Read-Only)',
        '修改文件': 'Edit File',
        '压缩上下文历史': 'Compact context history',
        '开始代码审查': 'Start code review',
        'Codex 额度': 'Codex Quota',
        '无限额度': 'Unlimited',
        '已完成当前 Codex 会话的上下文压缩。': 'Compacted current Codex session context.',
        '模型未生成最终回复，可点击「继续」接续执行。': 'No final reply generated. Tap "Resume" to continue.',
        '模型上游响应异常中止（未生成最终回复），可点击「继续」接续执行。': 'Upstream response ended unexpectedly. Tap "Resume" to continue.',
        '服务正在自动重启以应用最新更新...': 'Restarting server to apply latest updates...',
        '当前工作目录不是 Git 仓库': 'Current workspace is not a Git repository',
        'Agy 后台进程已退出，发送消息时将自动重启。': 'Agy background process exited; it will restart automatically on your next message.',
        '认证已过期或未登录，请先在电脑终端重新登录': 'Auth expired or not signed in. Please sign in again in your terminal.',
        '后端未启用 Web Push 依赖 (pywebpush)': 'Web Push (pywebpush) is not enabled on the server',
        '后端未启用 Web Push': 'Web Push is not enabled on the server',
        '将在': 'Creates in',
        '~/.config/agy/profiles/<名称>': '~/.config/agy/profiles/<name>',
        '~/.config/codex/profiles/<名称>': '~/.config/codex/profiles/<name>',
        '创建独立目录并自动复用已有 skills，再启动设备码登录。': 'an isolated directory reusing existing skills, then starts device-code login.',
        '编辑': 'Edit',
        '复制': 'Copy',
        '已复制': 'Copied',
        '已复制 ✓': 'Copied ✓',
        '复制失败': 'Copy failed',
        '复制失败，请手动选中复制': 'Copy failed, please copy manually',
        '点击复制': 'Tap to copy',
        '点击复制完整路径': 'Tap to copy full path',
        '已复制文件路径': 'File path copied',
        '引用': 'Quote',
        '引用回复': 'Quote',
        '引用此回复继续提问': 'Quote this reply',
        '引用选中内容': 'Quote selection',
        '取消引用 (Esc)': 'Clear quote (Esc)',
        '重试': 'Retry',
        '重新发送': 'Resend',
        '重新生成此回复': 'Regenerate reply',
        '复制消息': 'Copy message',
        '复制回复全文': 'Copy full reply',
        '回填到输入框编辑': 'Edit in composer',
        '定位到原消息': 'Jump to original message',
        '点击撤回到输入框编辑': 'Tap to move back to composer',
        '从队列移除': 'Remove from queue',
        '插队': 'Steer',
        '打断当前回合并立即插队发送': 'Interrupt current turn and send now',
        '立即发送此消息': 'Send this message now',
        '从侧边栏移除此会话': 'Remove session from sidebar',
        '恢复此会话到列表': 'Restore session to sidebar',
        '永久移除此会话': 'Remove session permanently',
        '全部恢复': 'Restore All',
        '归档会话失败': 'Failed to archive sessions',
        '恢复会话失败': 'Failed to restore sessions',
        '移除会话失败': 'Failed to remove session',
        '移除目录失败': 'Failed to remove directory',
        '请先重启后端服务以启用归档功能': 'Restart backend to enable archiving',
        '请先重启后端服务以启用归档恢复功能': 'Restart backend to enable unarchiving',
        '请先重启后端服务以启用移除功能': 'Restart backend to enable removal',
        '请先重启后端服务以启用密钥查看': 'Restart backend to enable token reveal',

        // Empty state & Chat
        'Agy 远程控制已就绪': 'VibeOne is ready',
        '直接发送指令，或从左侧栏切换任意项目目录与历史会话。': 'Send a prompt, or switch workspaces and sessions from the sidebar.',
        '↓ 回到最新': '↓ Latest',
        '加载更早记录': 'Load older messages',
        '正在加载更早记录...': 'Loading older messages...',
        '加载历史记录失败': 'Failed to load history',
        '当前会话已是最新': 'Session is already up to date',
        '连接已断开，正在重连...': 'Disconnected, reconnecting...',
        'WebSocket 未连接，请稍后重试': 'WebSocket not connected, please retry',
        '已发送中断请求': 'Stop requested',
        '已中断当前任务': 'Task stopped',
        '思考': 'Thinking',
        '思考过程': 'Thinking',
        '思考中...': 'Thinking...',
        '/btw 旁白': '/btw Side Q',
        '不入上下文': 'Isolated',
        '正在基于当前会话上下文回答...': 'Answering based on current session context...',
        '正在获取账号模型配额数据...': 'Fetching account model quota...',
        '上下文窗口': 'Context Window',
        '当前模型': 'Current Model',
        '工作目录': 'Workspace',
        '会话 ID': 'Session ID',
        '当前模式：': 'Current mode: ',
        '二进制文件，暂不支持文本预览': 'Binary file — text preview not supported',
        '无法预览文件': 'Cannot preview file',
        '加载文件失败': 'Failed to load file',
        '工具': 'Tools',
        '工具调用': 'Tool Calls',
        '正在加载思考详情...': 'Loading thinking details...',
        '正在加载工具详情...': 'Loading tool details...',
        '加载详情失败': 'Failed to load details',
        '处理中': 'Running',
        '处理中...': 'Processing...',
        '回答中...': 'Answering...',
        '回答失败': 'Failed to answer',
        '已完成 · 可随时关闭': 'Done · tap to close',
        '关闭旁白卡片': 'Close side card',
        '内容': 'Content',
        '我': 'Me',
        '(空消息)': '(Empty message)',
        '(等待输出...)': '(Waiting for output...)',
        '(无回复内容)': '(No response)',
        '未找到对应的提问内容': 'Original prompt not found',
        '点击展开/收起完整引用': 'Tap to expand/collapse quote',
        '执行命令': 'Run Command',
        '读文件': 'Read File',
        '写文件': 'Write File',
        '改文件': 'Edit File',
        '搜索': 'Search',

        // Composer & Pickers
        '发指令、粘贴或拖入图片，输入 / 呼出命令...': 'Send a prompt, paste/drop images, or type / for commands...',
        '上传图片 / 拍照': 'Upload image / photo',
        '图片上传失败': 'Image upload failed',
        '上传失败': 'Upload failed',
        '图片正在上传，请稍候...': 'Uploading image, please wait...',
        '发送': 'Send',
        '发送消息': 'Send message',
        '发送 (Shift+Enter)': 'Send (Shift+Enter)',
        '加入待发送队列': 'Add to queue',
        '中断当前任务': 'Stop current task',
        '待发送队列 (0)': 'Queue (0)',
        '快捷指令 (/)': 'Slash Commands (/)',
        '快捷': 'Quick',
        '模型': 'Model',
        '选择模型': 'Select Model',
        '返回模型列表': 'Back to models',
        '搜索模型名称或 ID...': 'Search model name or ID...',
        '选择推理档位 (Level)': 'Select reasoning level',
        '切换模型与思考档位': 'Switch model & reasoning level',
        '打开两级模型与思考档位选择器': 'Open model & reasoning picker',
        '暂无可用模型': 'No models available',
        '未找到匹配的模型': 'No matching models',
        '模型列表尚未加载': 'Model list not loaded yet',
        '执行模式': 'Mode',
        '关闭思考': 'No thinking',
        '极简思考': 'Minimal',
        '低 · 响应更快': 'Low · Faster',
        '中 · 均衡模式': 'Medium · Balanced',
        '高 · 深度推理': 'High · Deep reasoning',
        '极高 · 最强推理': 'XHigh · Max reasoning',
        '最高强度': 'Maximum',

        // Slash commands & local cards
        '本地即时命令': 'Local instant command',
        '临时旁白提问（不打断主任务、不入主上下文，看完可关闭）': 'Quick side question (isolated, does not interrupt main task)',
        '请输入旁白问题，例如：/btw 这个函数在哪被调用？': 'Enter a side question, e.g. /btw where is this function called?',
        '查看当前会话上下文用量、模型、模式与目录信息': 'Show session context usage, model, mode & workspace',
        '查看账号各模型分组的剩余配额与重置时间': 'Show account model quota & reset times',
        '切换当前 Agent 的执行模式': 'Switch execution mode for current agent',
        '选择目录创建新会话（支持 /new <路径>）': 'Pick a directory to start a new session (/new <path>)',
        '在当前目录立即开启一个全新的空白会话': 'Start a blank session in current workspace',
        '列出当前 Agent 可用的所有指令': 'List all available commands for current agent',
        '压缩当前对话历史，释放上下文窗口': 'Compact conversation history to free context window',
        '压缩当前 Codex 对话历史，释放上下文窗口': 'Compact Codex conversation history to free context',
        '从当前 Codex 会话分叉出一个新会话继续探索': 'Fork current Codex session into a new branch',
        '审查代码改动（默认未提交改动；支持 /review branch <分支> / commit <sha> / <要求>）': 'Review code changes (default: uncommitted; supports branch/commit)',
        '先制定详细执行计划再动手修改': 'Create a step-by-step plan before editing',
        '持续自主推进直到目标完全达成': 'Run autonomously until the goal is achieved',
        '设置定时任务或周期性提醒指令': 'Schedule a one-shot timer or recurring task',
        '调用浏览器代理执行网页交互与搜索': 'Use browser agent for web interaction & search',
        '通过互动问答帮我梳理设计细节与方案': 'Clarify design decisions via interactive Q&A',
        '启动多智能体协作完成复杂项目': 'Launch a multi-agent team for complex tasks',
        '总结本次经验并沉淀为可复用规则或技能': 'Distill lessons into reusable rules or skills',
        '调用 Boost 多视角深度思考与验证编排': 'Deep thinking, multi-perspective planning & verification',
        '/usage · 会话用量与状态': '/usage · Session Usage & Status',
        '/quota · 账号模型余量': '/quota · Account Model Quota',
        '/mode · 可用执行模式': '/mode · Available Modes',
        '5 小时限额': '5-Hour Limit',
        '每周限额': 'Weekly Limit',
        '切到此号': 'Switch',
        '暂无用量数据（发送消息后自动更新）': 'No usage data yet (updates after sending a message)',
        '接口未返回配额数据': 'No quota data returned',
        '未获取': 'Unavailable',
        '获取失败': 'Fetch failed',
        '提示': 'Notice',

        // Directory Picker, Git Diff, File Preview, Lightbox
        '浏览本机目录，选择目标项目并开启会话': 'Browse local folders and start a session',
        '↑ 上级': '↑ Up',
        '＋ 新建目录': '+ New Folder',
        '隐藏目录': 'Hidden',
        '前往': 'Go',
        '＋ 在当前目录创建新会话': '+ New Session Here',
        '正在读取目录...': 'Loading directory...',
        '读取目录失败': 'Failed to read directory',
        '获取目录失败，请重试': 'Failed to load directory, please retry',
        '创建目录失败': 'Failed to create directory',
        '此目录下暂无子文件夹，可直接点击下方按钮在此创建会话': 'No subfolders here — tap the button below to create a session.',
        '全部折叠': 'Collapse All',
        '全部展开': 'Expand All',
        '正在加载 Git Diff...': 'Loading Git Diff...',
        '获取 Git Diff 失败': 'Failed to load Git Diff',
        '暂无未提交代码改动': 'No uncommitted changes',
        '不是 Git 仓库': 'Not a Git repository',
        '点击展开加载代码改动...': 'Click to expand and load diff...',
        '正在加载文件改动...': 'Loading file diff...',
        '加载文件改动失败': 'Failed to load file diff',
        '无文本改动': 'No text changes',
        '内容过大，已截断显示': 'Diff too large, truncated for display',
        '非 Git 仓库': 'Not a Git repo',
        '文件预览': 'File Preview',
        '未知文件': 'Unknown file',
        '正在加载文件内容...': 'Loading file...',
        '空文件': 'Empty file',
        '源码': 'Source',
        '渲染': 'Render',
        '换行': 'Wrap',
        '切换自动换行': 'Toggle word wrap',
        '上一张': 'Prev',
        '下一张': 'Next',
        '点击还原 1x': 'Tap to reset 1x',
        '双指缩放 / 双击放大 · 下滑或点空白关闭': 'Pinch / double-tap to zoom · swipe down or tap backdrop to close',

        // Settings Modal
        '语言': 'Language',
        '切换界面显示语言（立即生效）': 'Switch interface display language (instant)',
        '会话管理、推送通知与账号安全': 'Sessions, push notifications & account security',
        '会话管理': 'Session Management',
        '查看或恢复已归档的历史会话': 'View or restore archived sessions',
        '已归档会话': 'Archived Sessions',
        '点击可查看会话，或恢复到左侧列表': 'Tap to open a session, or restore it to the sidebar',
        '推送通知': 'Push Notifications',
        '任务完成或需要审批时发送系统通知': 'Notify when tasks finish or need approval',
        '开启推送通知': 'Enable Push',
        '推送已开启': 'Push Enabled',
        '推送已被系统禁用': 'Push Blocked',
        '仅主屏幕PWA支持推送': 'Add to Home Screen for Push',
        '测试': 'Test',
        '发送一条测试推送': 'Send a test notification',
        '测试通知已发送，请查看系统通知栏': 'Test notification sent — check your notification center',
        '测试推送请求失败': 'Test push request failed',
        '推送通知已开启！可点「测试」验证': 'Push enabled! Tap "Test" to verify.',
        '已关闭推送通知': 'Push notifications disabled',
        '关闭推送失败': 'Failed to disable push',
        '订阅推送失败': 'Failed to subscribe to push',
        '发送失败，请重新开启推送订阅': 'Send failed — please re-enable push',
        '后端推送服务未就绪，请先重启后端服务': 'Push service not ready — restart backend first',
        '未授予通知权限，请在系统设置中允许通知': 'Notification permission denied — allow it in system settings',
        'iOS 需先将本页「添加到主屏幕」，再从桌面图标打开后开启推送': 'On iOS, add this page to your Home Screen first, then open it from the icon to enable Push.',
        '快捷解锁 (Passkey)': 'Quick Unlock (Passkey)',
        '绑定 Passkey 后可通过 iCloud 钥匙串 / Face ID 一键解锁': 'Bind a Passkey to unlock with Face ID / iCloud Keychain',
        '绑定后可通过 iCloud 钥匙串 / Face ID 一键解锁': 'Unlock with Face ID / iCloud Keychain',
        '绑定 Passkey（面容/iCloud）': 'Bind Passkey (Face ID / iCloud)',
        '访问密钥': 'Access Token',
        '用于在其他设备登录，默认隐藏，点击显示并复制': 'Used to sign in on other devices. Hidden by default.',
        '显示并复制': 'Reveal & Copy',
        '点击显示并复制访问密钥': 'Tap to reveal and copy access token',
        '获取中...': 'Loading...',
        '获取访问密钥失败': 'Failed to fetch access token',
        '已显示并复制访问密钥': 'Access token revealed & copied',
        '已复制访问密钥': 'Access token copied',
        '已显示密钥，请再次点击复制': 'Token revealed — tap again to copy',
        '重新隐藏密钥': 'Hide token',
        '账号': 'Account',
        '退出登录': 'Sign Out',

        // Profile Login Modal
        '账号登录': 'Account Sign-In',
        '免重启直接完成账号授权与热重载': 'Authorize and hot-reload without restarting',
        '创建独立配置目录并完成 Google OAuth 登录': 'Create an isolated profile directory & sign in via Google OAuth',
        '创建独立 CODEX_HOME 目录并完成设备码登录': 'Create an isolated CODEX_HOME & sign in via device code',
        '新账号配置名（例如 agy3、work）': 'New profile name (e.g. agy3, work)',
        '新 AGY 账号配置名（例如 agy3、work）': 'New AGY profile name (e.g. agy3, work)',
        '新 Codex 账号配置名（例如 codex2、codex-work）': 'New Codex profile name (e.g. codex2, codex-work)',
        '仅支持字母、数字、_ 和 -': 'Letters, numbers, _ and - only',
        '请输入合法的 AGY 账号名（字母、数字、_ 或 -，不能为 codex/default）': 'Enter a valid AGY profile name (letters, numbers, _, -; not codex/default)',
        '请输入合法的 Codex 账号名（字母、数字、_ 或 -，不能为 codex/agy/default）': 'Enter a valid Codex profile name (letters, numbers, _, -; not codex/agy/default)',
        '开始登录': 'Start Sign-In',
        '正在启动登录流程...': 'Starting sign-in flow...',
        '正在生成 Google OAuth 登录链接...': 'Generating Google OAuth link...',
        '正在启动 Codex 设备码登录...': 'Starting Codex device-code login...',
        'AGY Google OAuth 授权（完成后自动热重载）': 'AGY Google OAuth (auto hot-reload on finish)',
        'Codex 设备码授权（完成后自动热重载）': 'Codex Device Code (auto hot-reload on finish)',
        '第 1 步：在浏览器中打开授权页面': 'Step 1: Open the authorization page in your browser',
        '打开授权页面': 'Open Auth Page',
        '复制链接': 'Copy Link',
        '已复制授权链接': 'Auth link copied',
        '复制失败，请直接点击「打开授权页面」': 'Copy failed — tap "Open Auth Page" directly',
        '第 2 步：在授权页面输入一次性设备码': 'Step 2: Enter the one-time device code on the auth page',
        '复制设备码': 'Copy Code',
        '完成浏览器确认后，本窗口会自动检测登录完成并热重载 Codex。': 'After confirming in your browser, this window will detect completion and hot-reload Codex.',
        '第 2 步：粘贴授权码或跳转后的完整回调链接': 'Step 2: Paste the auth code or full callback URL',
        '粘贴浏览器显示的授权码，或直接粘贴整个 https://antigravity.google/oauth-callback?... 链接': 'Paste the auth code or the full https://antigravity.google/oauth-callback?... URL',
        '提交授权码并完成登录': 'Submit Code & Finish',
        '请先粘贴授权码或完整回调链接': 'Please paste the auth code or callback URL first',
        '重新发起': 'Restart',
        '重新登录 / 刷新凭证': 'Re-login / Refresh credentials',
        '立即登录': 'Sign in now',
        '保留当前': 'Keep Active',
        '创建独立目录并启动 Google OAuth 登录。': 'Create an isolated directory and start Google OAuth.',
        '请输入账号名称': 'Please enter a profile name',
        '账号名称仅支持字母、数字、下划线和短横线': 'Profile name may only contain letters, numbers, _ and -',
        '未检测到 codex CLI，请先安装 @openai/codex': 'codex CLI not found — please install @openai/codex first',
        '登录等待超时（10 分钟），请重试': 'Sign-in timed out (10 min), please retry',
        '请在浏览器打开授权链接并输入下方设备码': 'Open the auth link in your browser and enter the device code below',
        '请在浏览器完成 Google 登录，然后将授权码或完整回调链接粘贴到下方提交': 'Complete Google sign-in in your browser, then paste the auth code or full callback URL below',
        '已取消登录': 'Sign-in cancelled',
        '登录会话不存在或已过期': 'Login session not found or expired',
        '当前登录流程不需要手动提交授权码': 'This login flow does not require manual code submission',
        '请粘贴授权码或完整回调链接': 'Please paste the auth code or full callback URL',
        '登录进程已结束，请重新发起登录': 'Login process has ended — please restart sign-in',
        '正在提交授权码并验证凭证...': 'Submitting auth code and verifying credentials...',
        '登录会话不存在': 'Login session not found',
      };

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
