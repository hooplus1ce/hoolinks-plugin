/**
 * hoolinks-dsh-bridge — persistent DSH bundle.
 *
 * On host boot it reads the hoolinks-plugin repo (located by candidate root
 * resolution, overridable via `config.root` on the profile row), launches the
 * FastMCP stdio server declared in mcp.json through the subprocess service,
 * implements a minimal MCP stdio client (newline-delimited JSON-RPC), and
 * registers every server tool + the repo skills into DSH globally.
 *
 * Plain ESM, no external imports: uses ctx services (fs / subprocess / skills /
 * tools / timer) and standard globals only, mirroring how official tool
 * packages register capabilities.
 */

export const name = 'hoolinks-dsh-bridge';

const DEFAULT_ROOT = 'D:\\Developer\\Hoolinks\\hoolinks-plugin';

export async function apply(ctx, config) {
  const fsSvc = ctx.get('fs');
  const subSvc = ctx.get('subprocess');
  const skillsSvc = ctx.get('skills');
  const toolsSvc = ctx.get('tools');
  const sp = ctx.get('sandboxPolicy');
  const timer = ctx.get('timer');

  const state = {
    root: null,
    initDone: false,
    lastReload: null,
    child: null,
    client: null,
    clients: {},
    mcpInfo: null,
    mcpInfos: [],
    mcpSpecs: null,
    stderrTail: '',
    mcpToolNames: [],
    skipped: [],
    skillEntries: [],
    errors: [],
    toolDisposers: [],
    skillDisposers: [],
  };

  // teardown: terminate the MCP child when this row is disposed (stop/update)
  ctx.effect(() => {
    return () => {
      try { if (state.child) state.child.terminate(); } catch (e) {}
    };
  });

  async function resolveText(absPath) {
    const target = await fsSvc.resolve(absPath, {});
    return fsSvc.readText(target);
  }

  // pick the first candidate root that actually contains mcp.json
  async function resolveRoot() {
    const candidates = [];
    if (config && config.root) candidates.push(String(config.root));
    if (sp && sp.workspaceRoot) candidates.push(String(sp.workspaceRoot));
    candidates.push(DEFAULT_ROOT);
    if (fsSvc) {
      for (const cand of candidates) {
        try {
          const t = await fsSvc.resolve(cand + '\\mcp.json', {});
          await fsSvc.readText(t);
          return cand;
        } catch (e) {}
      }
    }
    return DEFAULT_ROOT;
  }

  // ---------- skills ----------
  function parseSkillMd(text) {
    const meta = {};
    if (typeof text === 'string' && text.startsWith('---')) {
      const end = text.indexOf('\n---', 3);
      if (end >= 0) {
        const fm = text.slice(3, end);
        for (const line of fm.split('\n')) {
          const idx = line.indexOf(':');
          if (idx <= 0) continue;
          const key = line.slice(0, idx).trim();
          let val = line.slice(idx + 1).trim();
          if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
            val = val.slice(1, -1);
          }
          if (val === 'true') val = true;
          else if (val === 'false') val = false;
          meta[key] = val;
        }
        return { meta, body: text.slice(end + 4).replace(/^\s*\n/, '') };
      }
    }
    return { meta, body: text };
  }

  function isKebab(name) {
    return /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name);
  }

  async function registerSkills() {
    if (!skillsSvc || !fsSvc) { state.errors.push('skills: fs or skills service unavailable'); return; }
    const dir = state.root + '\\skills';
    let entries;
    try {
      entries = await fsSvc.listDir(await fsSvc.resolve(dir, {}));
    } catch (e) {
      state.errors.push('skills: cannot list ' + dir + ': ' + e.message);
      return;
    }
    for (const entry of entries) {
      if (entry.type !== 'directory') continue;
      const skillDir = dir + '\\' + entry.name;
      let text;
      try { text = await resolveText(skillDir + '\\SKILL.md'); } catch (e) { continue; }
      const { meta, body } = parseSkillMd(text);
      const skillName = (typeof meta.name === 'string' && meta.name) ? meta.name : entry.name;
      if (!isKebab(skillName)) { state.errors.push('skills: invalid name ' + skillName); continue; }
      const disableModel = meta['disable-model-invocation'] === true || meta['disable-model-invocation'] === 'true';
      let disposer;
      try {
        disposer = skillsSvc.register({
          name: skillName,
          description: (typeof meta.description === 'string') ? meta.description : '',
          content: body,
          source: 'runtime',
          invocation: { modelInvocable: !disableModel, userInvocable: true },
          resourceBase: { kind: 'directory', path: skillDir },
        });
      } catch (e) {
        state.errors.push('skills: ' + skillName + ': ' + e.message);
        continue;
      }
      state.skillDisposers.push(disposer);
      const lines = body.split('\n');
      state.skillEntries.push({ name: skillName, contentLen: body.length, firstLine: lines[0] || '' });
    }
  }

  // ---------- JSON Schema -> ParameterSchemaSpec ----------
  function convertSchema(js) {
    if (!js || typeof js !== 'object') return { type: 'json' };
    const ann = {};
    if (typeof js.description === 'string') ann.description = js.description;
    if (Array.isArray(js.anyOf) || Array.isArray(js.oneOf)) {
      const src = Array.isArray(js.anyOf) ? js.anyOf : js.oneOf;
      const branches = src.filter((b) => !(b && b.type === 'null'));
      if (branches.length === 0) return { type: 'json', ...ann };
      if (branches.length === 1) return { ...convertSchema(branches[0]), ...ann };
      return { oneOf: branches.map((b) => convertSchema(b)), ...ann };
    }
    if (Array.isArray(js.enum) && js.enum.length) {
      const t = js.type || typeof js.enum[0];
      if (t === 'string' || t === 'number' || t === 'integer' || t === 'boolean' || t === 'null') {
        return { type: t, enum: js.enum, ...ann };
      }
      return { type: 'json', ...ann };
    }
    if (js.const !== undefined) {
      const t = js.type || typeof js.const;
      if (t === 'string' || t === 'number' || t === 'integer' || t === 'boolean' || t === 'null') {
        return { type: t, const: js.const, ...ann };
      }
      return { type: 'json', ...ann };
    }
    switch (js.type) {
      case 'string':
      case 'number':
      case 'integer':
      case 'boolean':
      case 'null':
        return { type: js.type, ...ann };
      case 'array': {
        const spec = { type: 'array', ...ann };
        if (js.items) spec.items = convertSchema(js.items);
        return spec;
      }
      case 'object': {
        const spec = { type: 'object', additionalProperties: js.additionalProperties === true, ...ann };
        if (js.properties && typeof js.properties === 'object') {
          spec.properties = {};
          for (const k of Object.keys(js.properties)) {
            spec.properties[k] = convertSchema(js.properties[k]);
          }
        }
        return spec;
      }
      default:
        return { type: 'json', ...ann };
    }
  }

  function convertParameters(inputSchema) {
    const params = {};
    if (!inputSchema || typeof inputSchema !== 'object' || !inputSchema.properties) return params;
    const required = Array.isArray(inputSchema.required) ? inputSchema.required : [];
    for (const k of Object.keys(inputSchema.properties)) {
      const spec = convertSchema(inputSchema.properties[k]);
      if (!spec) continue;
      if (required.indexOf(k) >= 0) spec.required = true;
      params[k] = spec;
    }
    return params;
  }

  // ---------- ParameterSchemaSpec -> object-rooted JSON Schema ----------
  // ctx.tools.register stores the definition as-is (unlike harness.defineTool,
  // which compiles); the model API requires parameters to be an object-rooted
  // JSON Schema, so compile the per-property map ourselves.
  function valueToJsonSchema(spec) {
    if (!spec || typeof spec !== 'object') return {};
    const out = {};
    if (spec.description !== undefined) out.description = spec.description;
    if (spec.enum !== undefined) out.enum = spec.enum;
    if (spec.const !== undefined) out.const = spec.const;
    switch (spec.type) {
      case 'string':
      case 'number':
      case 'integer':
      case 'boolean':
      case 'null':
        out.type = spec.type;
        return out;
      case 'array':
        out.type = 'array';
        if (spec.items) out.items = valueToJsonSchema(spec.items);
        return out;
      case 'object':
        out.type = 'object';
        out.additionalProperties = !!spec.additionalProperties;
        if (spec.properties) {
          out.properties = {};
          for (const k of Object.keys(spec.properties)) {
            out.properties[k] = valueToJsonSchema(spec.properties[k]);
          }
        }
        return out;
      case 'json':
        return out; // author-only 'json' node -> annotation-only (any value)
      default:
        if (Array.isArray(spec.oneOf) && spec.oneOf.length >= 2) {
          out.oneOf = spec.oneOf.map(valueToJsonSchema);
          return out;
        }
        return out;
    }
  }

  function paramsToJsonSchema(spec) {
    const properties = {};
    const required = [];
    for (const k of Object.keys(spec || {})) {
      properties[k] = valueToJsonSchema(spec[k]);
      if (spec[k] && spec[k].required) required.push(k);
    }
    return { type: 'object', properties: properties, required: required, additionalProperties: false };
  }

  // ---------- MCP HTTP (streamable-http / sse) client ----------
  // 桥接 mcp.json 中 type=streamable-http 的服务器（如腾讯文档 docs.qq.com/openapi/mcp）：
  // 走 JSON-RPC over HTTP POST，处理 JSON 与 text/event-stream 两种响应，
  // 透传自定义 headers（Authorization 等）与 Mcp-Session-Id 会话保持。
  function createHttpClient(spec) {
    const url = String(spec.url || '');
    const headers = {};
    for (const k of Object.keys(spec.headers || {})) {
      headers[k] = String(spec.headers[k]);
    }
    const client = {
      nextId: 1,
      pending: new Map(),
      ready: false,
      sessionId: null,
      protocolVersion: '2025-06-18',
      url,
      headers,
    };
    async function post(body) {
      const h = {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
        'MCP-Protocol-Version': client.protocolVersion,
        ...client.headers,
      };
      if (client.sessionId) h['Mcp-Session-Id'] = client.sessionId;
      const resp = await fetch(url, {
        method: 'POST',
        headers: h,
        body: JSON.stringify(body),
        redirect: 'follow',
      });
      const sid = resp.headers.get('mcp-session-id');
      if (sid) client.sessionId = sid;
      const ct = (resp.headers.get('content-type') || '').toLowerCase();
      const text = await resp.text();
      // SSE（text/event-stream）：逐行取 data: 负载；否则整体按 JSON 解析
      let parsed = null;
      if (ct.includes('text/event-stream')) {
        const datas = [];
        for (const line of text.split('\n')) {
          const l = line.trim();
          if (l.startsWith('data:')) {
            const payload = l.slice(5).trim();
            if (payload && payload !== '[DONE]') {
              try { datas.push(JSON.parse(payload)); } catch (e) {}
            }
          }
        }
        if (datas.length === 1) parsed = datas[0];
        else if (datas.length > 1) parsed = datas;
      } else {
        try { parsed = JSON.parse(text); } catch (e) {}
      }
      if (!resp.ok) {
        const errMsg = (parsed && (parsed.error && parsed.error.message)) || text || ('HTTP ' + resp.status);
        throw new Error('MCP HTTP ' + resp.status + ' ' + resp.statusText + ': ' + String(errMsg).slice(0, 300));
      }
      return parsed;
    }
    client.rpc = (method, params, timeoutMs) => {
      const id = client.nextId++;
      return new Promise((resolve, reject) => {
        let settled = false;
        let timerHandle = null;
        const done = (fn, v) => {
          if (settled) return;
          settled = true;
          if (timerHandle) timerHandle();
          fn(v);
        };
        if (timeoutMs && timer) {
          timerHandle = timer.timeout(() => {
            if (!settled) done(reject, new Error('MCP timeout: ' + method));
          }, timeoutMs);
        }
        (async () => {
          try {
            const body = { jsonrpc: '2.0', id: id, method: method, params: params || {} };
            const res = await post(body);
            if (Array.isArray(res)) {
              // 多事件流（如通知 + 响应）：取含 id 的响应
              const hit = res.find((r) => r && r.id === id);
              done(resolve, hit || null);
              return;
            }
            if (res && res.id !== undefined) {
              if (res.error) done(reject, new Error('MCP ' + (res.error.code !== undefined ? res.error.code + ' ' : '') + (res.error.message || JSON.stringify(res.error))));
              else done(resolve, res.result !== undefined ? res.result : res);
              return;
            }
            // 无 id（可能仅通知/事件）：resolve 结果本身
            done(resolve, res);
          } catch (e) {
            done(reject, e);
          }
        })();
      });
    };
    client.failAll = (reason) => {
      for (const entry of client.pending.values()) entry.reject(new Error(reason));
      client.pending.clear();
    };
    return client;
  }

  // ---------- MCP stdio client ----------
  function createClient() {
    const client = {
      nextId: 1,
      pending: new Map(),
      buf: '',
      decoder: new TextDecoder(),
      ready: false,
    };
    client.attach = (handle) => {
      client.handle = handle;
      handle.stdout.on('data', (chunk) => {
        client.buf += client.decoder.decode(chunk, { stream: true });
        let i;
        while ((i = client.buf.indexOf('\n')) >= 0) {
          const line = client.buf.slice(0, i).trim();
          client.buf = client.buf.slice(i + 1);
          if (!line) continue;
          let msg;
          try { msg = JSON.parse(line); } catch (e) { continue; }
          if (msg && msg.id !== undefined && client.pending.has(msg.id)) {
            const p = client.pending.get(msg.id);
            client.pending.delete(msg.id);
            if (msg.error) {
              p.reject(new Error('MCP ' + (msg.error.code !== undefined ? msg.error.code + ' ' : '') + (msg.error.message || JSON.stringify(msg.error))));
            } else {
              p.resolve(msg.result);
            }
          }
        }
      });
      handle.stderr.on('data', (chunk) => {
        state.stderrTail = (state.stderrTail + chunk.toString()).slice(-2000);
      });
      handle.done.then((outcome) => {
        client.failAll('MCP server exited: code=' + outcome.exitCode + ' signal=' + outcome.signal);
      }).catch(() => {});
    };
    client.send = (obj) => {
      if (!client.handle || !client.handle.stdin) throw new Error('MCP server stdin unavailable');
      client.handle.stdin.write(JSON.stringify(obj) + '\n');
    };
    client.rpc = (method, params, timeoutMs) => {
      const id = client.nextId++;
      return new Promise((resolve, reject) => {
        let settled = false;
        let timerHandle = null;
        const done = (fn, v) => {
          if (settled) return;
          settled = true;
          if (timerHandle) timerHandle();
          fn(v);
        };
        if (timeoutMs && timer) {
          timerHandle = timer.timeout(() => {
            if (client.pending.has(id)) {
              client.pending.delete(id);
              done(reject, new Error('MCP timeout: ' + method));
            }
          }, timeoutMs);
        }
        client.pending.set(id, {
          resolve: (v) => done(resolve, v),
          reject: (e) => done(reject, e),
        });
        try {
          client.send({ jsonrpc: '2.0', id: id, method: method, params: params || {} });
        } catch (e) {
          client.pending.delete(id);
          done(reject, e);
        }
      });
    };
    client.failAll = (reason) => {
      for (const entry of client.pending.values()) {
        entry.reject(new Error(reason));
      }
      client.pending.clear();
    };
    return client;
  }

  // ---------- MCP start ----------
  function resolveServerCwd(cwd, root) {
    if (!cwd) return root;
    let p = String(cwd).replace(/\$\{PLUGIN_ROOT\}/g, root).replace(/\$\{PLUGIN_DATA\}/g, root);
    if (p.startsWith('./')) p = p.slice(2);
    if (p.startsWith('.\\')) p = p.slice(2);
    return root + '\\' + p.replace(/\//g, '\\');
  }

  // 公共工具注册：stdio 与 HTTP 两条桥接路径共用
  function registerTools(tools, serverName, getClient) {
    let count = 0;
    const used = new Set();
    for (const t of tools || []) {
      if (!t || typeof t.name !== 'string' || !t.name) continue;
      const remoteName = t.name;
      // DSH 工具名仅允许 [a-zA-Z0-9_-]：腾讯文档等 MCP 工具名含点号（doc.insert_markdown），
      // 注册名清洗为下划线形式，远端调用仍用原始名。
      let localName = String(remoteName).replace(/[^a-zA-Z0-9_-]/g, '_');
      if (!localName) localName = 'tool';
      if (!/^[a-zA-Z0-9]/.test(localName)) localName = 't_' + localName;
      let candidate = localName;
      let i = 1;
      while (used.has(candidate)) {
        candidate = localName + '_' + (i++);
      }
      used.add(candidate);
      const params = convertParameters(t.inputSchema);
      const via = specTypeLabel(serverName);
      const description = ((typeof t.description === 'string' && t.description) ? t.description : remoteName)
        + '\n\n(via hoolinks-dsh-bridge: ' + via + ' tool, remote name: ' + remoteName + ')';
      const definition = {
        name: candidate,
        description: description,
        parameters: paramsToJsonSchema(params),
        output: {
          schema: { type: 'string' },
          render(_a, v) { return [{ type: 'text', text: v }]; },
        },
        timeoutMs: 180000,
        async execute(args, exec) {
          const cl = getClient();
          if (!cl || !cl.ready) throw new Error('hoolinks MCP server not ready');
          const result = await callMcp(cl, remoteName, args || {}, exec);
          const text = formatMcpResult(result);
          if (result && result.isError) throw new Error(text);
          return text;
        },
      };
      let disposer = null;
      try {
        if (toolsSvc && toolsSvc.register) {
          disposer = toolsSvc.register(definition);
        } else if (typeof harness !== 'undefined' && harness.registerTool) {
          disposer = harness.registerTool(ctx, harness.defineTool(definition));
        } else {
          state.errors.push('mcp.' + serverName + ': no tool registration seam');
          continue;
        }
      } catch (e) {
        state.errors.push('mcp.' + serverName + ': register ' + candidate + ': ' + e.message);
        continue;
      }
      state.toolDisposers.push(disposer);
      state.mcpToolNames.push(candidate + ' (=' + remoteName + ')');
      count++;
    }
    return count;
  }

  function specTypeLabel(serverName) {
    const spec = state.mcpSpecs && state.mcpSpecs[serverName];
    const type = spec ? spec.type : 'unknown';
    return type === 'streamable-http' ? 'MCP streamable-http' : 'FastMCP qa-automation tool';
  }

  async function bridgeStdioServer(serverName, spec) {
    const root = state.root;
    const cwd = resolveServerCwd(spec.cwd, root);
    const argv = [spec.command].concat(spec.args || []);
    let execPath = argv[0];
    if (subSvc && subSvc.resolveExecutable) {
      try { execPath = await subSvc.resolveExecutable(argv[0]); } catch (e) {}
    }
    const env = {};
    for (const k of Object.keys(spec.env || {})) {
      env[k] = String(spec.env[k]).replace(/\$\{PLUGIN_ROOT\}/g, root).replace(/\$\{PLUGIN_DATA\}/g, root);
    }
    env.PLUGIN_ROOT = root;
    env.PLUGIN_DATA = root;
    if (!('WORK_DIR' in env)) env.WORK_DIR = root;
    let handle;
    try {
      handle = subSvc.spawn({
        argv: [execPath].concat(argv.slice(1)),
        cwd: cwd,
        stdio: { stdin: 'pipe', stdout: 'pipe', stderr: 'pipe' },
        graceMs: 2000,
        env: env,
      });
    } catch (e) {
      state.errors.push('mcp.' + serverName + ': spawn failed: ' + e.message);
      return;
    }
    state.child = handle;
    const client = createClient();
    client.attach(handle);
    state.client = client;
    state.clients[serverName] = client;
    try {
      let init;
      try {
        init = await client.rpc('initialize', {
          protocolVersion: '2025-06-18',
          capabilities: {},
          clientInfo: { name: 'dsh-hoolinks-bridge', version: '0.1.0' },
        }, 20000);
      } catch (e) {
        init = await client.rpc('initialize', {
          protocolVersion: '2024-11-05',
          capabilities: {},
          clientInfo: { name: 'dsh-hoolinks-bridge', version: '0.1.0' },
        }, 20000);
      }
      client.send({ jsonrpc: '2.0', method: 'notifications/initialized' });
      client.ready = true;
      const list = await client.rpc('tools/list', {}, 20000);
      const tools = (list && list.tools) || [];
      state.mcpInfo = {
        serverName: (init.serverInfo && init.serverInfo.name) || serverName,
        protocol: init.protocolVersion || '?',
        pid: handle.pid,
        toolCount: tools.length,
      };
      state.mcpInfos.push(state.mcpInfo);
      registerTools(tools, serverName, () => state.clients[serverName]);
    } catch (e) {
      state.errors.push('mcp.' + serverName + ': handshake failed: ' + e.message);
      try { handle.terminate(); } catch (e2) {}
      state.child = null;
      state.client = null;
      delete state.clients[serverName];
    }
  }

  // streamable-http / sse 桥接：JSON-RPC over HTTP POST，无需子进程
  async function bridgeHttpServer(serverName, spec) {
    const client = createHttpClient(spec);
    state.client = client;
    state.clients[serverName] = client;
    try {
      let init;
      try {
        init = await client.rpc('initialize', {
          protocolVersion: '2025-06-18',
          capabilities: {},
          clientInfo: { name: 'dsh-hoolinks-bridge', version: '0.1.0' },
        }, 20000);
      } catch (e) {
        try {
          client.protocolVersion = '2024-11-05';
          init = await client.rpc('initialize', {
            protocolVersion: '2024-11-05',
            capabilities: {},
            clientInfo: { name: 'dsh-hoolinks-bridge', version: '0.1.0' },
          }, 20000);
        } catch (e2) {
          throw new Error('initialize failed (' + e.message + '; ' + e2.message + ')');
        }
      }
      client.ready = true;
      const list = await client.rpc('tools/list', {}, 20000);
      const tools = (list && list.tools) || [];
      state.mcpInfo = {
        serverName: (init && init.serverInfo && init.serverInfo.name) || serverName,
        protocol: client.protocolVersion,
        pid: null,
        toolCount: tools.length,
      };
      state.mcpInfos.push(state.mcpInfo);
      const registered = registerTools(tools, serverName, () => state.clients[serverName]);
      if (registered === 0 && tools.length > 0) {
        state.errors.push('mcp.' + serverName + ': tools listed but none registered');
      }
    } catch (e) {
      state.errors.push('mcp.' + serverName + ': http handshake failed: ' + e.message);
      state.client = null;
      state.mcpInfo = null;
      delete state.clients[serverName];
    }
  }

  async function startMcp() {
    if (!fsSvc || !subSvc) { state.errors.push('mcp: fs/subprocess service unavailable'); return; }
    let mcpJson;
    try {
      mcpJson = JSON.parse(await resolveText(state.root + '\\mcp.json'));
    } catch (e) {
      state.errors.push('mcp: cannot read mcp.json: ' + e.message);
      return;
    }
    state.mcpSpecs = (mcpJson && mcpJson.mcpServers) || {};
    for (const serverName of Object.keys(state.mcpSpecs)) {
      const s = state.mcpSpecs[serverName];
      if (!s) { state.skipped.push(serverName + ' (missing spec)'); continue; }
      if (s.type === 'stdio') {
        await bridgeStdioServer(serverName, s);
      } else if (s.type === 'streamable-http' || s.type === 'sse') {
        await bridgeHttpServer(serverName, s);
      } else {
        state.skipped.push(serverName + ' (' + s.type + ', not bridged)');
      }
    }
  }

  function callMcp(cl, toolName, args, exec) {
    return new Promise((resolve, reject) => {
      let settled = false;
      let signal = null;
      const done = (fn, v) => {
        if (settled) return;
        settled = true;
        if (signal) signal.removeEventListener('abort', onAbort);
        fn(v);
      };
      const onAbort = () => done(reject, new Error('hoolinks MCP call cancelled'));
      if (exec && exec.signal) {
        signal = exec.signal;
        signal.addEventListener('abort', onAbort);
      }
      cl.rpc('tools/call', { name: toolName, arguments: args }, 120000).then(
        (v) => done(resolve, v),
        (e) => done(reject, e),
      );
    });
  }

  function formatMcpResult(result) {
    if (result === null || result === undefined) return '(empty result)';
    const parts = [];
    if (Array.isArray(result.content)) {
      for (const c of result.content) {
        if (!c || typeof c !== 'object') { parts.push(JSON.stringify(c)); continue; }
        if (c.type === 'text') parts.push(c.text || '');
        else if (c.type === 'image') parts.push('[image ' + (c.mimeType || '') + ' ' + ((c.data || '').length) + ' bytes omitted]');
        else if (c.type === 'resource') {
          const r = c.resource || {};
          parts.push('[resource ' + (r.uri || r.text || '') + ']');
        } else parts.push(JSON.stringify(c));
      }
    }
    if (result.structuredContent !== undefined) parts.push('[structured] ' + JSON.stringify(result.structuredContent));
    const text = parts.length ? parts.join('\n') : JSON.stringify(result);
    return (result.isError ? '[MCP error] ' : '') + text;
  }

  // ---------- admin tools ----------
  function buildStatus() {
    return {
      plugin: 'hoolinks-dsh-bridge',
      root: state.root,
      initDone: state.initDone,
      lastReload: state.lastReload,
      mcp: state.mcpInfos.length ? state.mcpInfos : state.mcpInfo,
      mcpServers: state.mcpInfos,
      mcpToolCount: state.mcpToolNames.length,
      mcpTools: state.mcpToolNames,
      skippedServers: state.skipped,
      skills: state.skillEntries,
      errors: state.errors,
      stderrTail: state.stderrTail,
    };
  }

  function registerAdminTool(name, description, execute) {
    const definition = {
      name: name,
      description: description,
      parameters: paramsToJsonSchema({}),
      output: {
        schema: { type: 'string' },
        render(_a, v) { return [{ type: 'text', text: v }]; },
      },
      execute: execute,
    };
    try {
      if (toolsSvc && toolsSvc.register) {
        const d = toolsSvc.register(definition);
        state.toolDisposers.push(d);
      } else if (typeof harness !== 'undefined' && harness.registerTool) {
        const d = harness.registerTool(ctx, harness.defineTool(definition));
        state.toolDisposers.push(d);
      }
    } catch (e) {
      state.errors.push('admin ' + name + ': ' + e.message);
    }
  }

  registerAdminTool('hoolinks_status',
    'Show live status of the hoolinks-dsh-bridge: MCP server state, registered MCP tool names, registered skills, and errors.',
    async () => JSON.stringify(buildStatus(), null, 2));

  registerAdminTool('hoolinks_reload',
    'Reload the hoolinks-dsh-bridge: re-read mcp.json and skills/ from disk, restart the MCP server process, and re-register every MCP tool and skill with the latest content.',
    async () => {
      await initAll();
      return JSON.stringify(buildStatus(), null, 2);
    });

  // ---------- init / reload ----------
  function disposeAll() {
    for (const d of state.toolDisposers) { try { d(); } catch (e) {} }
    state.toolDisposers = [];
    for (const d of state.skillDisposers) { try { d(); } catch (e) {} }
    state.skillDisposers = [];
    try { if (state.child) state.child.terminate(); } catch (e) {}
    state.child = null;
    state.client = null;
    state.clients = {};
    state.mcpInfo = null;
    state.mcpInfos = [];
    state.mcpToolNames = [];
    state.skillEntries = [];
    state.errors = [];
    state.skipped = [];
    state.stderrTail = '';
  }

  async function initAll() {
    disposeAll();
    try {
      state.root = await resolveRoot();
    } catch (e) {
      state.errors.push('root: ' + e.message);
      state.root = DEFAULT_ROOT;
    }
    try { await registerSkills(); } catch (e) { state.errors.push('skills: ' + e.message); }
    try { await startMcp(); } catch (e) { state.errors.push('mcp: ' + e.message); }
    state.initDone = true;
    state.lastReload = new Date().toISOString();
    console.log('[hoolinks-dsh-bridge] init done: mcpTools=' + state.mcpToolNames.length
      + ' skills=' + state.skillEntries.length + ' errors=' + state.errors.length + ' root=' + state.root);
  }

  await initAll();
}
