const state = {
  activeJob: null,
  pollTimer: null,
  mediaKey: null,
  artifactKey: null,
  harnessCursor: '0',
  harnessEvents: [],
  harnessPendingCache: null,
  harnessPollTimer: null,
  harnessRequestGeneration: 0,
  harnessRunId: '',
  harnessSubmissionGeneration: 0,
  harnessTerminalExpectation: null,
};

const $ = (selector) => document.querySelector(selector);
const artifactUrl = (job, path) => `/api/jobs/${job.job_id}/artifacts/${path.split('/').map(encodeURIComponent).join('/')}`;
const harnessCachePrefix = 'robot-harness.workbench-event-cache.v1';
const harnessCacheKey = () => `${harnessCachePrefix}:${state.harnessRunId || 'all'}`;
const sqliteCursorMaximum = BigInt('9223372036854775807');
const canonicalUuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function isCanonicalHarnessCursor(value) {
  return typeof value === 'string'
    && /^(0|[1-9][0-9]*)$/.test(value)
    && BigInt(value) <= sqliteCursorMaximum;
}

function harnessCursorIsAfter(value, preceding) {
  return BigInt(value) > BigInt(preceding);
}

function precedingHarnessCursor(value) {
  if (!isCanonicalHarnessCursor(value) || value === '0') invalidHarnessPage();
  return (BigInt(value) - 1n).toString();
}

function removeHarnessCache() {
  try {
    window.localStorage.removeItem(harnessCacheKey());
  } catch (_error) {
    // Storage is optional; the authoritative journal remains available without it.
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(value.error?.message || `HTTP ${response.status}`);
    error.code = value.error?.code;
    error.status = response.status;
    throw error;
  }
  return value;
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { 'aria-hidden': 'true' } });
}

function restoreHarnessCache() {
  state.harnessPendingCache = null;
  try {
    const cached = JSON.parse(window.localStorage.getItem(harnessCacheKey()));
    if (!cached) return;
    if (cached.schema_version !== 'harness.workbench_event_cache.v1'
      || !isCanonicalHarnessCursor(cached.cursor)
      || !Array.isArray(cached.events)
      || cached.events.length > 200
      || (!cached.events.length && cached.cursor !== '0')) throw new Error('invalid cache');
    const firstEventId = cached.events[0]?.event_id;
    const precedingCursor = firstEventId ? precedingHarnessCursor(firstEventId) : '0';
    validateHarnessPage({
      schema_version: 'harness.workbench_event_page.v1',
      events: cached.events,
      last_event_id: cached.cursor,
      has_more: false,
    }, precedingCursor);
    if (cached.events.length) state.harnessPendingCache = cached;
  } catch (_error) {
    removeHarnessCache();
    state.harnessCursor = '0';
    state.harnessEvents = [];
    state.harnessPendingCache = null;
  }
}

function saveHarnessCache() {
  state.harnessEvents = state.harnessEvents.slice(-200);
  try {
    window.localStorage.setItem(harnessCacheKey(), JSON.stringify({
      schema_version: 'harness.workbench_event_cache.v1',
      cursor: state.harnessCursor,
      events: state.harnessEvents,
    }));
  } catch (_error) {
    // The live journal remains authoritative when browser storage is unavailable.
  }
}

function initializeHarnessFilter() {
  state.harnessRunId = new URL(window.location.href).searchParams.get('harness_run')?.trim() || '';
  const input = $('#harness-run-filter');
  input.value = state.harnessRunId;
  if (state.harnessRunId) input.setAttribute('value', state.harnessRunId);
  else input.removeAttribute('value');
}

function selectHarnessRun(runId, terminalExpectation = null) {
  clearTimeout(state.harnessPollTimer);
  state.harnessRequestGeneration += 1;
  state.harnessRunId = runId.trim();
  state.harnessTerminalExpectation = terminalExpectation;
  state.harnessCursor = '0';
  state.harnessEvents = [];
  state.harnessPendingCache = null;
  const input = $('#harness-run-filter');
  input.value = state.harnessRunId;
  if (state.harnessRunId) input.setAttribute('value', state.harnessRunId);
  else input.removeAttribute('value');
  const url = new URL(window.location.href);
  if (state.harnessRunId) url.searchParams.set('harness_run', state.harnessRunId);
  else url.searchParams.delete('harness_run');
  window.history.replaceState({}, '', url);
  restoreHarnessCache();
  renderHarnessEvents();
  loadHarnessEvents();
}

function invalidHarnessPage() {
  const error = new Error('Harness event page failed integrity checks');
  error.code = 'harness_event_page_invalid';
  throw error;
}

function hasExactKeys(value, expected) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === [...expected].sort()[index]);
}

function invalidHarnessSubmission() {
  const error = new Error('Harness compile response failed integrity checks');
  error.code = 'harness_compile_response_invalid';
  throw error;
}

function validateHarnessSubmission(submission) {
  const fields = ['attempt', 'blocker', 'max_attempts', 'run_id', 'schema_version', 'skill_id',
    'skill_version', 'status', 'terminal_event_id'];
  const terminalStatuses = new Set(['succeeded', 'blocked', 'failed']);
  const attemptContract = (submission?.attempt === 0
      && submission?.max_attempts === 0
      && submission?.status !== 'succeeded')
    || (submission?.attempt === 1 && submission?.max_attempts === 1);
  if (!hasExactKeys(submission, fields)
    || submission.schema_version !== 'harness.workbench_compile_submission.v1'
    || !canonicalUuidPattern.test(submission.run_id)
    || submission.skill_id !== 'text2env.compile'
    || submission.skill_version !== '1.0.0'
    || !terminalStatuses.has(submission.status)
    || !Number.isInteger(submission.attempt)
    || !Number.isInteger(submission.max_attempts)
    || !attemptContract
    || !isCanonicalHarnessCursor(submission.terminal_event_id)
    || submission.terminal_event_id === '0') invalidHarnessSubmission();
  if (submission.status === 'succeeded') {
    if (submission.blocker !== null) invalidHarnessSubmission();
  } else if (!hasExactKeys(submission.blocker, ['code', 'retryable'])
    || typeof submission.blocker.code !== 'string'
    || !/^[A-Z][A-Z0-9_]{1,63}$/.test(submission.blocker.code)
    || typeof submission.blocker.retryable !== 'boolean') invalidHarnessSubmission();
  return submission;
}

function verifyHarnessTerminalExpectation() {
  const expected = state.harnessTerminalExpectation;
  if (!expected) return;
  const terminal = state.harnessEvents.at(-1);
  if (state.harnessRunId !== expected.run_id
    || state.harnessCursor !== expected.terminal_event_id
    || !terminal
    || terminal.run_id !== expected.run_id
    || terminal.skill_id !== expected.skill_id
    || terminal.skill_version !== expected.skill_version
    || terminal.event.to_status !== expected.status
    || terminal.event.attempt !== expected.attempt) invalidHarnessPage();
  state.harnessTerminalExpectation = null;
  const status = $('#harness-submit-status');
  status.textContent = `Harness Compile 已持久化为 ${expected.status} · ${expected.run_id}`;
  status.className = 'form-status ready';
}

function validateHarnessPage(page, afterEventId, expectedRunId = state.harnessRunId) {
  if (!page
    || page.schema_version !== 'harness.workbench_event_page.v1'
    || !Array.isArray(page.events)
    || !isCanonicalHarnessCursor(page.last_event_id)
    || BigInt(page.last_event_id) < BigInt(afterEventId)
    || typeof page.has_more !== 'boolean') invalidHarnessPage();
  const statuses = new Set(['running', 'succeeded', 'blocked', 'failed']);
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  let previousEventId = afterEventId;
  page.events.forEach((envelope) => {
    const event = envelope?.event;
    if (!isCanonicalHarnessCursor(envelope?.event_id)
      || envelope.event_id === '0'
      || !harnessCursorIsAfter(envelope.event_id, previousEventId)
      || typeof envelope.skill_id !== 'string'
      || !envelope.skill_id
      || typeof envelope.skill_version !== 'string'
      || !envelope.skill_version
      || typeof envelope.run_id !== 'string'
      || !uuidPattern.test(envelope.run_id)
      || (expectedRunId && envelope.run_id !== expectedRunId)
      || !event
      || !Number.isInteger(event.seq)
      || event.seq < 1
      || !Number.isInteger(event.attempt)
      || event.attempt < 0
      || typeof event.stage !== 'string'
      || !event.stage
      || typeof event.timestamp !== 'string'
      || !statuses.has(event.to_status)
      || (event.from_status !== null && !statuses.has(event.from_status))
      || !Array.isArray(event.artifact_refs)) invalidHarnessPage();
    previousEventId = envelope.event_id;
  });
  if (page.last_event_id !== previousEventId || (page.has_more && !page.events.length)) {
    invalidHarnessPage();
  }
  return page;
}

function renderHarnessEvents() {
  const list = $('#harness-event-list');
  list.replaceChildren();
  state.harnessEvents.forEach((envelope) => {
    const event = envelope.event;
    const item = document.createElement('li');
    item.dataset.eventId = String(envelope.event_id);
    item.className = `event-item ${event.to_status}`;

    const heading = document.createElement('div');
    heading.className = 'event-heading';
    const cursor = document.createElement('span');
    cursor.className = 'event-cursor';
    cursor.textContent = `#${envelope.event_id}`;
    const stage = document.createElement('strong');
    stage.textContent = event.stage;
    const status = document.createElement('span');
    status.className = `event-status ${event.to_status}`;
    status.textContent = event.to_status;
    heading.append(cursor, stage, status);

    const details = document.createElement('div');
    details.className = 'event-details';
    const skill = document.createElement('span');
    skill.textContent = `${envelope.skill_id}@${envelope.skill_version}`;
    const run = document.createElement('code');
    run.textContent = envelope.run_id;
    const attempt = document.createElement('span');
    attempt.textContent = `attempt ${event.attempt} · seq ${event.seq}`;
    details.append(skill, run, attempt);
    if (event.artifact_refs.length) {
      const artifacts = document.createElement('span');
      artifacts.textContent = `${event.artifact_refs.length} artifact${event.artifact_refs.length === 1 ? '' : 's'}`;
      details.appendChild(artifacts);
    }
    item.append(heading, details);
    list.appendChild(item);
  });
  if (!state.harnessEvents.length) {
    const empty = document.createElement('li');
    empty.className = 'event-empty';
    empty.textContent = '暂无已提交事件';
    list.appendChild(empty);
  }
  $('#harness-cursor').textContent = String(state.harnessCursor);
}

async function loadHarnessEvents() {
  clearTimeout(state.harnessPollTimer);
  const requestGeneration = ++state.harnessRequestGeneration;
  const requestedRunId = state.harnessRunId;
  const status = $('#harness-feed-status');
  const message = $('#harness-feed-message');
  try {
    const pendingCache = state.harnessPendingCache;
    const requestedCursor = pendingCache?.events.length
      ? precedingHarnessCursor(pendingCache.events[0].event_id)
      : state.harnessCursor;
    const query = new URLSearchParams({
      after: requestedCursor,
      limit: pendingCache?.events.length ? String(pendingCache.events.length) : '100',
    });
    if (requestedRunId) query.set('run_id', requestedRunId);
    const page = validateHarnessPage(
      await api(`/api/harness/events?${query}`),
      requestedCursor,
      requestedRunId,
    );
    if (requestGeneration !== state.harnessRequestGeneration
      || requestedRunId !== state.harnessRunId) return;
    if (pendingCache) {
      if (pendingCache !== state.harnessPendingCache
        || page.last_event_id !== pendingCache.cursor
        || JSON.stringify(page.events) !== JSON.stringify(pendingCache.events)) {
        invalidHarnessPage();
      }
      state.harnessCursor = pendingCache.cursor;
      state.harnessEvents = pendingCache.events;
      state.harnessPendingCache = null;
    } else {
      page.events.forEach((envelope) => {
        if (harnessCursorIsAfter(envelope.event_id, state.harnessCursor)) {
          state.harnessEvents.push(envelope);
        }
      });
      state.harnessCursor = page.last_event_id;
    }
    saveHarnessCache();
    status.textContent = '已连接';
    status.className = 'feed-status ready';
    message.textContent = '只显示执行代码已经提交到 Harness journal 的真实事件。';
    renderHarnessEvents();
    if (page.has_more) {
      await loadHarnessEvents();
      return;
    }
    verifyHarnessTerminalExpectation();
    state.harnessPollTimer = setTimeout(loadHarnessEvents, 1500);
  } catch (error) {
    if (requestGeneration !== state.harnessRequestGeneration
      || requestedRunId !== state.harnessRunId) return;
    const labels = {
      harness_event_feed_corrupt: '历史损坏',
      harness_event_feed_unavailable: '未配置',
      harness_event_page_invalid: '响应损坏',
      invalid_harness_event_query: '筛选无效',
    };
    status.textContent = labels[error.code] || '不可用';
    status.className = 'feed-status failed';
    message.textContent = error.message;
    const failClosedCodes = ['harness_event_feed_corrupt', 'harness_event_feed_unavailable',
      'harness_event_page_invalid', 'invalid_harness_event_query'];
    const failClosed = failClosedCodes.includes(error.code);
    if (failClosed) {
      removeHarnessCache();
      state.harnessCursor = '0';
      state.harnessEvents = [];
      state.harnessPendingCache = null;
    }
    if (state.harnessTerminalExpectation && failClosed) {
      state.harnessTerminalExpectation = null;
      const submissionStatus = $('#harness-submit-status');
      submissionStatus.textContent = 'Harness Compile 摘要与 committed journal 无法对账。';
      submissionStatus.className = 'form-status failed';
    } else if (state.harnessTerminalExpectation) {
      const submissionStatus = $('#harness-submit-status');
      submissionStatus.textContent = 'committed journal 暂时不可用；保留摘要并等待重试。';
      submissionStatus.className = 'form-status';
    }
    renderHarnessEvents();
    if (!failClosed) state.harnessPollTimer = setTimeout(loadHarnessEvents, 5000);
  }
}

function statusClass(status, verdict) {
  if (status === 'completed') return verdict === 'pass' ? 'pass' : 'fail';
  if (status === 'failed') return 'fail';
  if (status === 'running' || status === 'queued') return 'running';
  return '';
}

function renderPipeline(job) {
  document.querySelectorAll('#pipeline li').forEach((item) => {
    const stage = item.dataset.stage;
    const status = job.stages?.[stage]?.status || 'pending';
    item.className = status;
    const icon = item.querySelector('svg, i');
    if (icon) icon.setAttribute('data-lucide', status === 'completed' ? 'circle-check' : status === 'failed' ? 'circle-x' : status === 'running' ? 'loader-circle' : 'circle');
  });
  const active = job.active_stage && job.stages?.[job.active_stage];
  const output = job.error
    ? `${job.error.type}: ${job.error.message}`
    : job.status === 'completed'
      ? `Completed: compile, RoboTwin runtime, and rendered-scene critic. Verdict: ${job.verdict}.`
      : active?.output || (job.status === 'queued' ? 'Queued on the RTX 5090 worker.' : 'Waiting for stage output.');
  $('#stage-output').textContent = output;
  refreshIcons();
}

function mediaEntries(job) {
  const labels = {
    video: ['10 s · 120 frames', 'video'],
    head: ['Head camera', 'image'],
    world_left: ['World left', 'image'],
    world_right: ['World right', 'image'],
    observer_start: ['Frame 001', 'image'],
    observer_mid: ['Frame 060', 'image'],
    observer_end: ['Frame 120', 'image'],
  };
  return Object.entries(labels)
    .filter(([key]) => job.artifacts?.[key])
    .map(([key, [label, kind]]) => ({ key, label, kind, path: job.artifacts[key] }));
}

function artifactEntries(job) {
  const labels = {
    scene_spec: 'SceneSpec',
    resolved_scene: 'Resolved',
    static_validation: 'Static validation',
    asset_generation: 'Assets',
    runtime_evidence: 'Physics',
    runtime_validation: 'Validation',
    critic: 'VLM critic',
  };
  return Object.entries(labels)
    .filter(([key]) => job.artifacts?.[key])
    .map(([key, label]) => ({ key, label, path: job.artifacts[key] }));
}

function selectMedia(job, entry) {
  state.mediaKey = entry.key;
  document.querySelectorAll('.media-button').forEach((button) => button.setAttribute('aria-selected', String(button.dataset.key === entry.key)));
  const stage = $('#media-stage');
  stage.replaceChildren();
  const element = document.createElement(entry.kind === 'video' ? 'video' : 'img');
  if (entry.kind === 'video') {
    element.controls = true;
    element.autoplay = true;
    element.loop = true;
    element.muted = true;
    element.playsInline = true;
    element.preload = 'auto';
    const poster = job.artifacts?.world_left || job.artifacts?.head;
    if (poster) element.poster = artifactUrl(job, poster);
  } else {
    element.alt = `${entry.label} simulation evidence`;
  }
  element.src = artifactUrl(job, entry.path);
  stage.appendChild(element);
  if (entry.kind === 'video') {
    element.load();
    void element.play().catch(() => {});
  }
}

async function selectArtifact(job, entry) {
  state.artifactKey = entry.key;
  document.querySelectorAll('.artifact-button').forEach((button) => button.setAttribute('aria-selected', String(button.dataset.key === entry.key)));
  $('#json-view').textContent = 'Loading…';
  try {
    const response = await fetch(artifactUrl(job, entry.path));
    const value = await response.json();
    $('#json-view').textContent = JSON.stringify(value, null, 2);
  } catch (error) {
    $('#json-view').textContent = String(error);
  }
}

function renderEvidence(job) {
  const section = $('#evidence-band');
  if (job.status !== 'completed') {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const verdict = $('#verdict');
  verdict.textContent = job.verdict === 'pass' ? 'Runtime + VLM pass' : 'Critic review failed';
  verdict.className = `verdict ${job.verdict === 'pass' ? 'pass' : 'fail'}`;

  const media = mediaEntries(job);
  const toolbar = $('#media-toolbar');
  toolbar.replaceChildren();
  media.forEach((entry) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'media-button';
    button.dataset.key = entry.key;
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-selected', String(entry.key === (state.mediaKey || media[0]?.key)));
    button.textContent = entry.label;
    button.addEventListener('click', () => selectMedia(job, entry));
    toolbar.appendChild(button);
  });
  const selectedMedia = media.find((entry) => entry.key === state.mediaKey) || media[0];
  if (selectedMedia) selectMedia(job, selectedMedia);

  const artifacts = artifactEntries(job);
  const tabs = $('#artifact-tabs');
  tabs.replaceChildren();
  artifacts.forEach((entry) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'artifact-button';
    button.dataset.key = entry.key;
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-selected', String(entry.key === (state.artifactKey || artifacts[0]?.key)));
    button.textContent = entry.label;
    button.addEventListener('click', () => selectArtifact(job, entry));
    tabs.appendChild(button);
  });
  const selectedArtifact = artifacts.find((entry) => entry.key === state.artifactKey) || artifacts[0];
  if (selectedArtifact) selectArtifact(job, selectedArtifact);
}

function renderJob(job) {
  state.activeJob = job;
  $('#empty-run').hidden = true;
  $('#active-run').hidden = false;
  $('#job-id').textContent = `JOB ${job.job_id} · SEED ${job.seed}`;
  $('#job-prompt').textContent = job.prompt;
  const badge = $('#job-status');
  badge.textContent = job.status === 'completed' ? job.verdict : job.status;
  badge.className = `status-badge ${statusClass(job.status, job.verdict)}`;
  const openButton = $('#open-result-button');
  openButton.hidden = job.status !== 'completed';
  renderPipeline(job);
  renderEvidence(job);
  const url = new URL(window.location.href);
  url.searchParams.set('job', job.job_id);
  window.history.replaceState({}, '', url);
}

async function loadJob(jobId) {
  try {
    const job = await api(`/api/jobs/${jobId}`);
    renderJob(job);
    if (['queued', 'running'].includes(job.status)) schedulePoll(jobId);
    else clearTimeout(state.pollTimer);
  } catch (error) {
    $('#form-error').hidden = false;
    $('#form-error').textContent = error.message;
  }
}

function schedulePoll(jobId) {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(async () => {
    await loadJob(jobId);
    await loadHistory();
  }, 2000);
}

async function loadHistory() {
  const container = $('#history-list');
  try {
    const { jobs } = await api('/api/jobs?limit=12');
    container.replaceChildren();
    if (!jobs.length) {
      const empty = document.createElement('div');
      empty.className = 'history-empty';
      empty.textContent = '暂无作业';
      container.appendChild(empty);
      return;
    }
    jobs.forEach((job) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'history-item';
      const copy = document.createElement('div');
      copy.className = 'history-copy';
      const prompt = document.createElement('strong');
      prompt.textContent = job.prompt;
      const id = document.createElement('span');
      id.className = 'job-id';
      id.textContent = job.job_id;
      copy.append(prompt, id);
      const meta = document.createElement('div');
      meta.className = 'history-meta';
      const seed = document.createElement('span');
      seed.textContent = `SEED ${job.seed}`;
      const status = document.createElement('span');
      status.textContent = job.status === 'completed' ? job.verdict : job.status;
      meta.append(seed, status);
      button.append(copy, meta);
      button.addEventListener('click', () => {
        state.mediaKey = null;
        state.artifactKey = null;
        loadJob(job.job_id);
        window.scrollTo({ top: $('#run-title').offsetTop - 24, behavior: 'smooth' });
      });
      container.appendChild(button);
    });
  } catch (error) {
    container.textContent = error.message;
  }
}

async function checkHealth() {
  const element = $('#system-state');
  try {
    const health = await api('/api/health');
    element.className = `system-state ${health.status === 'ready' ? 'ready' : 'failed'}`;
    $('#system-state-label').textContent = health.status === 'ready' ? '系统就绪' : '路径未就绪';
  } catch (_error) {
    element.className = 'system-state failed';
    $('#system-state-label').textContent = '服务不可用';
  }
}

$('#scene-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('#generate-button');
  const error = $('#form-error');
  button.disabled = true;
  error.hidden = true;
  try {
    const job = await api('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ prompt: $('#prompt').value, seed: Number($('#seed').value) }),
    });
    state.mediaKey = null;
    state.artifactKey = null;
    renderJob(job);
    schedulePoll(job.job_id);
    await loadHistory();
  } catch (exception) {
    error.textContent = exception.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
});

$('#harness-compile-button').addEventListener('click', async () => {
  const button = $('#harness-compile-button');
  const status = $('#harness-submit-status');
  const submissionGeneration = ++state.harnessSubmissionGeneration;
  clearTimeout(state.harnessPollTimer);
  state.harnessRequestGeneration += 1;
  state.harnessTerminalExpectation = null;
  button.disabled = true;
  status.textContent = 'Harness Compile 请求处理中；尚未显示任何执行阶段。';
  status.className = 'form-status';
  try {
    const submission = validateHarnessSubmission(await api('/api/harness/compile', {
      method: 'POST',
      body: JSON.stringify({ request: $('#prompt').value, seed: Number($('#seed').value) }),
    }));
    if (submissionGeneration !== state.harnessSubmissionGeneration) return;
    status.textContent = '已收到终态摘要，正在从 committed journal 重放。';
    selectHarnessRun(submission.run_id, submission);
  } catch (error) {
    if (submissionGeneration !== state.harnessSubmissionGeneration) return;
    status.textContent = error.message;
    status.className = 'form-status failed';
    loadHarnessEvents();
  } finally {
    if (submissionGeneration === state.harnessSubmissionGeneration) button.disabled = false;
  }
});

$('#example-select').addEventListener('change', (event) => {
  if (event.target.value) $('#prompt').value = event.target.value;
});
$('#refresh-button').addEventListener('click', () => state.activeJob && loadJob(state.activeJob.job_id));
$('#harness-refresh-button').addEventListener('click', loadHarnessEvents);
$('#harness-filter-form').addEventListener('submit', (event) => {
  event.preventDefault();
  selectHarnessRun($('#harness-run-filter').value);
});
$('#harness-filter-clear').addEventListener('click', () => selectHarnessRun(''));
$('#open-result-button').addEventListener('click', () => {
  if (state.activeJob) window.open(`/?job=${encodeURIComponent(state.activeJob.job_id)}`, '_blank', 'noopener');
});

async function start() {
  refreshIcons();
  await Promise.all([checkHealth(), loadHistory(), loadHarnessEvents()]);
  const jobId = new URL(window.location.href).searchParams.get('job');
  if (jobId) await loadJob(jobId);
}

initializeHarnessFilter();
restoreHarnessCache();
renderHarnessEvents();
start();
