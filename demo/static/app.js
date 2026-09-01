const state = {
  activeJob: null,
  pollTimer: null,
  mediaKey: null,
  artifactKey: null,
  harnessCursor: '0',
  harnessEvents: [],
  harnessAuditGeneration: 0,
  harnessAuditKey: null,
  harnessAuditRetryTimer: null,
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
const canonicalTimestampPattern = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-](\d{2}):(\d{2}))$/;
const compileDependencyVersions = [
  ['asset-library-state', '1'],
  ['catalog-selected-assets', '1'],
  ['ledger-contract', '1'],
  ['scene-gen', '0.1.0'],
  ['text2env-compile-config', '1'],
];
const compileArtifactBindings = [
  ['input', 'asset_catalog'],
  ['output', 'scene_spec'],
  ['output', 'resolved_scene'],
  ['output', 'environment_package.asset_catalog'],
  ['output', 'environment_package.package_manifest'],
  ['output', 'static_validation'],
];
const compileArtifactBindingMetadata = [
  ['application/json', 'robotwin.asset_catalog.v1'],
  ['application/json', 'robotwin.scene_spec.v1'],
  ['application/json', 'robotwin.resolved_scene.v1'],
  ['application/json', 'robotwin.asset_catalog.v1'],
  ['application/json', 'robotwin.generated_scene_package.v1'],
  ['application/json', 'robotwin.scene_validation.v1'],
];
const artifactNamePattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const artifactMediaTypePattern = /^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}\/[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$/;
const artifactSchemaPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const artifactDigestPattern = /^[0-9a-f]{64}$/;
const artifactByteCountPattern = /^(0|[1-9][0-9]*)$/;

function isCanonicalHarnessCursor(value) {
  return typeof value === 'string'
    && /^(0|[1-9][0-9]*)$/.test(value)
    && BigInt(value) <= sqliteCursorMaximum;
}

function harnessCursorIsAfter(value, preceding) {
  return BigInt(value) > BigInt(preceding);
}

function harnessTimestampKey(value) {
  if (typeof value !== 'string') return null;
  const match = canonicalTimestampPattern.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = match[8] === 'Z' ? 0 : Number(match[9]);
  const offsetMinute = match[8] === 'Z' ? 0 : Number(match[10]);
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth[month - 1]
    || hour > 23 || minute > 59 || second > 59
    || offsetHour > 23 || offsetMinute > 59) return null;
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) return null;
  const subMillisecond = (match[7] || '').padEnd(6, '0').slice(3);
  return BigInt(milliseconds) * 1000n + BigInt(subMillisecond || '0');
}

function isCanonicalHarnessTimestamp(value) {
  return harnessTimestampKey(value) !== null;
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
  if (state.harnessRunId) return;
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
  if (state.harnessRunId) {
    removeHarnessCache();
    return;
  }
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
  resetHarnessAudit();
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

function invalidHarnessAudit() {
  const error = new Error('Harness compile audit failed integrity checks');
  error.code = 'harness_compile_audit_invalid';
  throw error;
}

function validateHarnessEventArtifact(artifact, invalid = invalidHarnessPage) {
  if (!hasExactKeys(artifact, ['bytes', 'media_type', 'name', 'schema_version', 'sha256', 'uri'])
    || typeof artifact.name !== 'string'
    || !artifactNamePattern.test(artifact.name)
    || typeof artifact.media_type !== 'string'
    || !artifactMediaTypePattern.test(artifact.media_type)
    || (artifact.schema_version !== null
      && (typeof artifact.schema_version !== 'string'
        || !artifactSchemaPattern.test(artifact.schema_version)))
    || typeof artifact.sha256 !== 'string'
    || !artifactDigestPattern.test(artifact.sha256)
    || !Number.isSafeInteger(artifact.bytes)
    || artifact.bytes < 0
    || artifact.uri !== `artifact://sha256/${artifact.sha256}`) invalid();
  return artifact;
}

function harnessArtifactIdentity(artifact) {
  return `${artifact.media_type}\u0000${artifact.schema_version || ''}\u0000${artifact.sha256}`;
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
  if (!hasExactKeys(page, ['events', 'has_more', 'last_event_id', 'schema_version'])
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
    if (!hasExactKeys(envelope, ['event', 'event_id', 'run_id', 'skill_id', 'skill_version'])
      || !hasExactKeys(event, ['artifact_refs', 'attempt', 'from_status', 'seq', 'stage',
        'timestamp', 'to_status'])
      || !isCanonicalHarnessCursor(envelope?.event_id)
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
      || !isCanonicalHarnessTimestamp(event.timestamp)
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

function resetHarnessAudit(message = '筛选一个已终止的 Compile run 后显示依赖与终态对账。') {
  clearTimeout(state.harnessAuditRetryTimer);
  state.harnessAuditRetryTimer = null;
  state.harnessAuditGeneration += 1;
  state.harnessAuditKey = null;
  const panel = $('#harness-audit-panel');
  const summary = $('#harness-audit-run-summary');
  const dependencies = $('#harness-dependency-list');
  const artifacts = $('#harness-artifact-list');
  if (panel) panel.hidden = true;
  if (summary) summary.replaceChildren();
  if (dependencies) dependencies.replaceChildren();
  if (artifacts) artifacts.replaceChildren();
  const artifactCount = $('#harness-artifact-count');
  if (artifactCount) artifactCount.textContent = '';
  const invocationStatus = $('#harness-invocation-status');
  const invocationDigest = $('#harness-invocation-digest');
  if (invocationStatus) invocationStatus.textContent = '';
  if (invocationDigest) invocationDigest.textContent = '';
  const auditMessage = $('#harness-audit-message');
  if (auditMessage) auditMessage.textContent = message;
}

function selectedTerminalCompileHistory() {
  if (!canonicalUuidPattern.test(state.harnessRunId) || !state.harnessEvents.length) return null;
  let previousEvent = null;
  let previousTimestamp = null;
  state.harnessEvents.forEach((envelope, index) => {
    const event = envelope.event;
    const timestamp = harnessTimestampKey(event.timestamp);
    if (envelope.run_id !== state.harnessRunId
      || event.seq !== index + 1
      || timestamp === null
      || (previousTimestamp !== null && timestamp < previousTimestamp)) invalidHarnessPage();
    if (index === 0) {
      if (event.from_status !== null || event.to_status !== 'running') invalidHarnessPage();
    } else if (event.from_status !== previousEvent.to_status
      || previousEvent.to_status !== 'running'
      || event.attempt < previousEvent.attempt
      || event.attempt > previousEvent.attempt + 1) invalidHarnessPage();
    if (index > 0 && (envelope.skill_id !== state.harnessEvents[0].skill_id
      || envelope.skill_version !== state.harnessEvents[0].skill_version)) invalidHarnessPage();
    previousEvent = event;
    previousTimestamp = timestamp;
  });
  const first = state.harnessEvents[0];
  if (first.skill_id !== 'text2env.compile' || first.skill_version !== '1.0.0') return null;
  const terminal = state.harnessEvents.at(-1);
  return terminal.event.to_status === 'running' ? null : terminal;
}

function validateHarnessAuditArtifact(artifact) {
  if (!hasExactKeys(
    artifact,
    ['bindings', 'bytes', 'media_type', 'name', 'schema_version', 'sha256'],
  )
    || typeof artifact.name !== 'string'
    || !artifactNamePattern.test(artifact.name)
    || typeof artifact.media_type !== 'string'
    || !artifactMediaTypePattern.test(artifact.media_type)
    || (artifact.schema_version !== null
      && (typeof artifact.schema_version !== 'string'
        || !artifactSchemaPattern.test(artifact.schema_version)))
    || typeof artifact.sha256 !== 'string'
    || !artifactDigestPattern.test(artifact.sha256)
    || typeof artifact.bytes !== 'string'
    || !artifactByteCountPattern.test(artifact.bytes)
    || !Array.isArray(artifact.bindings)) invalidHarnessAudit();
  let previousBindingIndex = -1;
  artifact.bindings.forEach((binding) => {
    if (!hasExactKeys(binding, ['direction', 'role'])) invalidHarnessAudit();
    const bindingIndex = compileArtifactBindings.findIndex(
      ([direction, role]) => binding.direction === direction && binding.role === role,
    );
    if (bindingIndex <= previousBindingIndex
      || artifact.media_type !== compileArtifactBindingMetadata[bindingIndex]?.[0]
      || artifact.schema_version !== compileArtifactBindingMetadata[bindingIndex]?.[1]) {
      invalidHarnessAudit();
    }
    previousBindingIndex = bindingIndex;
  });
  return artifact;
}

function validateHarnessAudit(audit, runId, events, cursor) {
  const runFields = ['attempt', 'blocker', 'ended_at', 'event_count', 'max_attempts', 'run_id',
    'skill_id', 'skill_version', 'started_at', 'status', 'terminal_event_id'];
  const invocationFields = ['dependencies', 'digest', 'status'];
  const terminalStatuses = new Set(['succeeded', 'blocked', 'failed']);
  if (!hasExactKeys(audit, ['artifacts', 'invocation', 'run', 'schema_version'])
    || audit.schema_version !== 'harness.workbench_compile_audit.v2'
    || !hasExactKeys(audit.run, runFields)
    || !hasExactKeys(audit.invocation, invocationFields)
    || !Array.isArray(audit.artifacts)
    || audit.artifacts.length > 500) invalidHarnessAudit();
  const run = audit.run;
  const invocation = audit.invocation;
  if (run.run_id !== runId
    || run.skill_id !== 'text2env.compile'
    || run.skill_version !== '1.0.0'
    || !terminalStatuses.has(run.status)
    || !Number.isInteger(run.attempt)
    || !Number.isInteger(run.max_attempts)
    || !Number.isInteger(run.event_count)
    || run.event_count !== events.length
    || run.event_count < 1
    || run.terminal_event_id !== cursor
    || !isCanonicalHarnessCursor(run.terminal_event_id)
    || !isCanonicalHarnessTimestamp(run.started_at)
    || !isCanonicalHarnessTimestamp(run.ended_at)) invalidHarnessAudit();
  if (run.status === 'succeeded') {
    if (run.blocker !== null) invalidHarnessAudit();
  } else if (!hasExactKeys(run.blocker, ['code', 'retryable'])
    || typeof run.blocker.code !== 'string'
    || !/^[A-Z][A-Z0-9_]{1,63}$/.test(run.blocker.code)
    || typeof run.blocker.retryable !== 'boolean') invalidHarnessAudit();
  const first = events[0];
  const terminal = events.at(-1);
  events.forEach((event) => {
    event.event.artifact_refs.forEach(
      (artifact) => validateHarnessEventArtifact(artifact, invalidHarnessAudit),
    );
  });
  if (terminal.event_id !== cursor
    || terminal.run_id !== runId
    || terminal.skill_id !== run.skill_id
    || terminal.skill_version !== run.skill_version
    || terminal.event.to_status !== run.status
    || terminal.event.attempt !== run.attempt
    || first.event.timestamp !== run.started_at
    || terminal.event.timestamp !== run.ended_at) invalidHarnessAudit();
  if (!Array.isArray(invocation.dependencies)) invalidHarnessAudit();
  if (invocation.status === 'not_created_preflight') {
    if (run.attempt !== 0 || run.max_attempts !== 0 || run.status === 'succeeded'
      || invocation.digest !== null || invocation.dependencies.length
      || audit.artifacts.length
      || events.some((event) => event.event.attempt !== 0 || event.event.artifact_refs.length)) {
      invalidHarnessAudit();
    }
  } else if (invocation.status === 'bound') {
    if (run.attempt !== 1 || run.max_attempts !== 1
      || typeof invocation.digest !== 'string'
      || !/^[0-9a-f]{64}$/.test(invocation.digest)
      || events.some((event) => event.event.attempt !== 1)) invalidHarnessAudit();
  } else {
    invalidHarnessAudit();
  }
  const dependencyNames = [];
  invocation.dependencies.forEach((dependency) => {
    if (!hasExactKeys(dependency, ['name', 'sha256', 'version'])
      || typeof dependency.name !== 'string'
      || !dependency.name
      || typeof dependency.version !== 'string'
      || !dependency.version
      || typeof dependency.sha256 !== 'string'
      || !/^[0-9a-f]{64}$/.test(dependency.sha256)) invalidHarnessAudit();
    dependencyNames.push(dependency.name);
  });
  const dependencyVersions = invocation.dependencies.map(
    (dependency) => [dependency.name, dependency.version],
  );
  if (invocation.status === 'bound'
    && (JSON.stringify(dependencyVersions) !== JSON.stringify(compileDependencyVersions)
      || dependencyNames.length !== compileDependencyVersions.length)) invalidHarnessAudit();
  const artifactIdentities = new Set();
  const observedBindings = [];
  audit.artifacts.forEach((artifact) => {
    validateHarnessAuditArtifact(artifact);
    const identity = harnessArtifactIdentity(artifact);
    if (artifactIdentities.has(identity)) invalidHarnessAudit();
    artifactIdentities.add(identity);
    artifact.bindings.forEach((binding) => {
      const pair = [binding.direction, binding.role];
      if (observedBindings.some((existing) => JSON.stringify(existing) === JSON.stringify(pair))) {
        invalidHarnessAudit();
      }
      observedBindings.push(pair);
    });
  });
  if (invocation.status === 'bound') {
    const terminalArtifacts = events.at(-1).event.artifact_refs;
    if (terminalArtifacts.length !== audit.artifacts.length) invalidHarnessAudit();
    audit.artifacts.forEach((artifact, index) => {
      const terminalArtifact = terminalArtifacts[index];
      if (artifact.name !== terminalArtifact.name
        || artifact.media_type !== terminalArtifact.media_type
        || artifact.schema_version !== terminalArtifact.schema_version
        || artifact.sha256 !== terminalArtifact.sha256
        || artifact.bytes !== String(terminalArtifact.bytes)) invalidHarnessAudit();
    });
    events.forEach((event) => {
      event.event.artifact_refs.forEach((artifact) => {
        const auditArtifact = audit.artifacts.find(
          (candidate) => harnessArtifactIdentity(candidate) === harnessArtifactIdentity(artifact),
        );
        if (!auditArtifact
          || auditArtifact.media_type !== artifact.media_type
          || auditArtifact.schema_version !== artifact.schema_version
          || auditArtifact.sha256 !== artifact.sha256
          || auditArtifact.bytes !== String(artifact.bytes)) invalidHarnessAudit();
      });
    });
    if (run.status === 'succeeded') {
      const normalizedBindings = [...observedBindings].sort((left, right) => {
        const leftIndex = compileArtifactBindings.findIndex(
          ([direction, role]) => left[0] === direction && left[1] === role,
        );
        const rightIndex = compileArtifactBindings.findIndex(
          ([direction, role]) => right[0] === direction && right[1] === role,
        );
        return leftIndex - rightIndex;
      });
      if (JSON.stringify(normalizedBindings) !== JSON.stringify(compileArtifactBindings)) {
        invalidHarnessAudit();
      }
    } else if (observedBindings.some(([direction]) => direction === 'output')) {
      invalidHarnessAudit();
    }
  }
  return audit;
}

function appendAuditSummary(summary, label, value) {
  const term = document.createElement('dt');
  term.textContent = label;
  const description = document.createElement('dd');
  description.textContent = value;
  summary.append(term, description);
}

function renderHarnessAudit(audit) {
  const summary = $('#harness-audit-run-summary');
  summary.replaceChildren();
  appendAuditSummary(summary, 'Run', audit.run.run_id);
  appendAuditSummary(summary, 'Status', audit.run.status);
  appendAuditSummary(summary, 'Attempt', `${audit.run.attempt}/${audit.run.max_attempts}`);
  appendAuditSummary(summary, 'Events', `${audit.run.event_count} · #${audit.run.terminal_event_id}`);
  appendAuditSummary(summary, 'Started', audit.run.started_at);
  appendAuditSummary(summary, 'Ended', audit.run.ended_at);
  $('#harness-invocation-status').textContent = audit.invocation.status === 'bound'
    ? 'Invocation 已绑定'
    : '未创建 Invocation（预检终止）';
  $('#harness-invocation-digest').textContent = audit.invocation.digest || '';
  const dependencies = $('#harness-dependency-list');
  dependencies.replaceChildren();
  audit.invocation.dependencies.forEach((dependency) => {
    const item = document.createElement('li');
    const name = document.createElement('strong');
    name.textContent = `${dependency.name}@${dependency.version}`;
    const digest = document.createElement('code');
    digest.textContent = dependency.sha256;
    item.append(name, digest);
    dependencies.appendChild(item);
  });
  if (!audit.invocation.dependencies.length) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = '预检终止前未解析依赖。';
    dependencies.appendChild(empty);
  }
  const artifacts = $('#harness-artifact-list');
  artifacts.replaceChildren();
  $('#harness-artifact-count').textContent = `${audit.artifacts.length}`;
  audit.artifacts.forEach((artifact) => {
    const item = document.createElement('li');
    item.className = 'compile-artifact-item';
    const name = document.createElement('strong');
    name.textContent = artifact.name;
    const metadata = document.createElement('span');
    metadata.textContent = `${artifact.media_type} · ${artifact.schema_version || 'untyped'} · ${artifact.bytes} bytes`;
    const bindings = document.createElement('span');
    bindings.className = 'compile-artifact-bindings';
    bindings.textContent = artifact.bindings.length
      ? artifact.bindings.map((binding) => `${binding.direction} · ${binding.role}`).join(' | ')
      : 'supporting artifact';
    const digest = document.createElement('code');
    digest.textContent = artifact.sha256;
    item.append(name, metadata, bindings, digest);
    artifacts.appendChild(item);
  });
  if (!audit.artifacts.length) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = audit.invocation.status === 'not_created_preflight'
      ? '预检终止前未产出 artifact。'
      : '此终态没有 committed artifact metadata。';
    artifacts.appendChild(empty);
  }
  $('#harness-audit-message').textContent = '摘要已与完整 committed journal 对账；artifact metadata 已复核。';
  $('#harness-audit-panel').hidden = false;
}

async function loadHarnessAuditIfEligible() {
  const terminal = selectedTerminalCompileHistory();
  if (!terminal) {
    if (!state.harnessRunId) resetHarnessAudit();
    else if (!canonicalUuidPattern.test(state.harnessRunId)) {
      resetHarnessAudit('Run ID 无效；未请求审计。');
    } else if (state.harnessEvents.length
      && state.harnessEvents[0].skill_id !== 'text2env.compile') {
      resetHarnessAudit('当前 run 不是 text2env.compile@1.0.0。');
    } else {
      resetHarnessAudit('等待 committed 终态后再显示审计。');
    }
    return;
  }
  const runId = state.harnessRunId;
  const auditKey = `${runId}:${state.harnessCursor}:${state.harnessEvents.length}`;
  if (state.harnessAuditKey === auditKey) return;
  state.harnessAuditKey = auditKey;
  const generation = ++state.harnessAuditGeneration;
  $('#harness-audit-panel').hidden = true;
  $('#harness-audit-message').textContent = '正在与 committed journal 对账…';
  try {
    const audit = await api(`/api/harness/compile-runs/${encodeURIComponent(runId)}/audit`);
    if (generation !== state.harnessAuditGeneration
      || runId !== state.harnessRunId
      || auditKey !== `${state.harnessRunId}:${state.harnessCursor}:${state.harnessEvents.length}`) {
      return;
    }
    renderHarnessAudit(validateHarnessAudit(
      audit,
      runId,
      state.harnessEvents,
      state.harnessCursor,
    ));
  } catch (error) {
    if (generation !== state.harnessAuditGeneration || runId !== state.harnessRunId) return;
    $('#harness-audit-panel').hidden = true;
    $('#harness-audit-message').textContent = '终态与 committed journal 无法对账。';
    if (!error.code || error.code === 'harness_compile_audit_unavailable') {
      state.harnessAuditRetryTimer = setTimeout(() => {
        if (generation !== state.harnessAuditGeneration || runId !== state.harnessRunId) return;
        state.harnessAuditKey = null;
        loadHarnessAuditIfEligible();
      }, 5000);
    }
  }
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
      limit: pendingCache?.events.length
        ? String(pendingCache.events.length)
        : (requestedRunId ? '500' : '100'),
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
    await loadHarnessAuditIfEligible();
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
    resetHarnessAudit('终态与 committed journal 无法对账。');
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
  resetHarnessAudit('等待新的 Compile run 完成并写入 committed journal。');
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
$('#harness-refresh-button').addEventListener('click', () => {
  resetHarnessAudit('正在重新读取 committed journal。');
  loadHarnessEvents();
});
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
resetHarnessAudit();
start();
