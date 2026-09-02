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
  harnessScenePreviewController: null,
  harnessScenePreviewGeneration: 0,
  harnessStaticValidationController: null,
  harnessStaticValidationGeneration: 0,
  harnessVerifiedAudit: null,
  harnessPendingCache: null,
  harnessPollTimer: null,
  harnessRequestGeneration: 0,
  harnessRunId: '',
  harnessSubmissionGeneration: 0,
  harnessTerminalExpectation: null,
};

const $ = (selector) => document.querySelector(selector);
const harnessRunBoard = window.HarnessRunBoard.mount({
  root: $('#harness-run-board'),
  onInspect: (runId) => selectHarnessRun(runId),
});
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
const scenePreviewArtifactMaximumBytes = 65_536n;
const scenePreviewResponseMaximumBytes = 262_144;
const staticValidationArtifactMaximumBytes = 262_144n;
const staticValidationResponseMaximumBytes = 262_144;
const staticValidationMaximumChecks = 169;
const staticValidationCheckNamePattern = /^[a-z][a-z0-9_]{0,63}(?::[a-z][a-z0-9_]{0,63}){0,3}$/;
const scenePreviewSceneIdPattern = /^[a-z][a-z0-9_-]{0,95}$/;
const scenePreviewObjectIdPattern = /^[a-z][a-z0-9_]{0,63}$/;
const scenePreviewTargetPattern = /^(table|[a-z][a-z0-9_]{0,63})$/;
const scenePreviewCategoryPattern = /^[a-z][a-z0-9_-]{0,63}$/;
const scenePreviewAttributePattern = /^[a-z][a-z0-9_-]{0,31}$/;

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

function invalidHarnessScenePreview() {
  const error = new Error('Harness compile scene preview failed integrity checks');
  error.code = 'harness_compile_scene_preview_invalid';
  throw error;
}

function invalidHarnessStaticValidationPreview() {
  const error = new Error('Harness compile static validation preview failed integrity checks');
  error.code = 'harness_compile_static_validation_preview_invalid';
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

function resetHarnessScenePreview({ forgetAudit = true, message = '', returnFocus = false } = {}) {
  if (state.harnessScenePreviewController) state.harnessScenePreviewController.abort();
  state.harnessScenePreviewController = null;
  state.harnessScenePreviewGeneration += 1;
  const button = $('#harness-scene-preview-button');
  const panel = $('#harness-scene-preview-panel');
  const contents = $('#harness-scene-preview-content');
  const status = $('#harness-scene-preview-message');
  if (button) {
    button.disabled = false;
    button.setAttribute('aria-expanded', 'false');
    if (forgetAudit) button.hidden = true;
  }
  if (panel) {
    panel.hidden = true;
    panel.setAttribute('aria-busy', 'false');
  }
  if (contents) contents.replaceChildren();
  if (status) status.textContent = message;
  if (forgetAudit) state.harnessVerifiedAudit = null;
  if (returnFocus && button && !button.hidden) button.focus();
}

function resetHarnessStaticValidationPreview({
  forgetAudit = true,
  message = '',
  returnFocus = false,
} = {}) {
  if (state.harnessStaticValidationController) {
    state.harnessStaticValidationController.abort();
  }
  state.harnessStaticValidationController = null;
  state.harnessStaticValidationGeneration += 1;
  const button = $('#harness-static-validation-button');
  const panel = $('#harness-static-validation-panel');
  const summary = $('#harness-static-validation-summary');
  const checks = $('#harness-static-validation-checks');
  const status = $('#harness-static-validation-message');
  if (button) {
    button.disabled = false;
    button.setAttribute('aria-expanded', 'false');
    if (forgetAudit) button.hidden = true;
  }
  if (panel) {
    panel.hidden = true;
    panel.setAttribute('aria-busy', 'false');
  }
  if (summary) summary.replaceChildren();
  if (checks) checks.replaceChildren();
  if (status) status.textContent = message;
  if (forgetAudit) state.harnessVerifiedAudit = null;
  if (returnFocus && button) button.focus();
}

function resetHarnessAudit(message = '筛选一个已终止的 Compile run 后显示依赖与终态对账。') {
  resetHarnessScenePreview();
  resetHarnessStaticValidationPreview();
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

function validateScenePreviewString(
  value,
  { nullable = false, maximum = 4096, pattern = null } = {},
) {
  if (nullable && value === null) return value;
  if (typeof value !== 'string'
    || !value.length
    || value.length > maximum
    || (pattern && !pattern.test(value))) {
    invalidHarnessScenePreview();
  }
  return value;
}

function validateScenePreviewNumber(value, { nullable = false } = {}) {
  if (nullable && value === null) return value;
  if (typeof value !== 'number' || !Number.isFinite(value)) invalidHarnessScenePreview();
  return value;
}

function validateScenePreviewPair(value) {
  if (!Array.isArray(value) || value.length !== 2) invalidHarnessScenePreview();
  value.forEach((item) => validateScenePreviewNumber(item));
  if (value[0] >= value[1]) invalidHarnessScenePreview();
  return value;
}

function scenePreviewHasCycle(nodes, edges) {
  const graph = new Map([...nodes].map((node) => [node, []]));
  edges.forEach(([source, target]) => graph.get(source).push(target));
  const visiting = new Set();
  const visited = new Set();
  const visit = (node) => {
    if (visiting.has(node)) return true;
    if (visited.has(node)) return false;
    visiting.add(node);
    if (graph.get(node).some((target) => visit(target))) return true;
    visiting.delete(node);
    visited.add(node);
    return false;
  };
  return [...nodes].some((node) => visit(node));
}

function validateSceneProjection(scene) {
  if (!hasExactKeys(
    scene,
    ['frame', 'language', 'objects', 'relations', 'scene_id', 'seed', 'unit', 'workspace'],
  )) invalidHarnessScenePreview();
  validateScenePreviewString(scene.scene_id, {
    maximum: 96,
    pattern: scenePreviewSceneIdPattern,
  });
  if (!['en', 'zh', 'mixed'].includes(scene.language)
    || scene.unit !== 'm'
    || !Number.isSafeInteger(scene.seed)
    || scene.seed < 0
    || scene.seed > 2_147_483_647) invalidHarnessScenePreview();
  if (!hasExactKeys(scene.frame, ['handedness', 'name', 'x_axis', 'y_axis', 'z_axis'])
    || scene.frame.name !== 'robotwin_world'
    || scene.frame.x_axis !== 'right'
    || scene.frame.y_axis !== 'front'
    || scene.frame.z_axis !== 'up'
    || scene.frame.handedness !== 'right_handed') invalidHarnessScenePreview();
  if (!hasExactKeys(
    scene.workspace,
    ['robot_keepout_x_m', 'robot_keepout_y_m', 'support_surface', 'table_height_m',
      'x_bounds_m', 'y_bounds_m'],
  ) || scene.workspace.support_surface !== 'table') invalidHarnessScenePreview();
  validateScenePreviewNumber(scene.workspace.table_height_m);
  if (scene.workspace.table_height_m < 0.5 || scene.workspace.table_height_m > 1.2) {
    invalidHarnessScenePreview();
  }
  validateScenePreviewPair(scene.workspace.x_bounds_m);
  validateScenePreviewPair(scene.workspace.y_bounds_m);
  validateScenePreviewPair(scene.workspace.robot_keepout_x_m);
  validateScenePreviewPair(scene.workspace.robot_keepout_y_m);
  if (!Array.isArray(scene.objects) || !scene.objects.length || scene.objects.length > 12) {
    invalidHarnessScenePreview();
  }
  const objectIds = new Set();
  scene.objects.forEach((object) => {
    if (!hasExactKeys(
      object,
      ['articulation', 'category', 'color', 'material', 'object_id', 'region'],
    )) invalidHarnessScenePreview();
    validateScenePreviewString(object.object_id, {
      maximum: 64,
      pattern: scenePreviewObjectIdPattern,
    });
    validateScenePreviewString(object.category, {
      maximum: 64,
      pattern: scenePreviewCategoryPattern,
    });
    validateScenePreviewString(object.color, {
      nullable: true,
      maximum: 32,
      pattern: scenePreviewAttributePattern,
    });
    validateScenePreviewString(object.material, {
      nullable: true,
      maximum: 32,
      pattern: scenePreviewAttributePattern,
    });
    if (objectIds.has(object.object_id)) invalidHarnessScenePreview();
    objectIds.add(object.object_id);
    if (!['left', 'right', 'front', 'back', 'center'].includes(object.region)) {
      invalidHarnessScenePreview();
    }
    if (object.articulation !== null) {
      if (!hasExactKeys(
        object.articulation,
        ['joint_selector', 'open_fraction', 'state'],
      )
        || !['closed', 'open', 'partially_open'].includes(object.articulation.state)
        || !['all_movable', 'first_movable'].includes(object.articulation.joint_selector)) {
        invalidHarnessScenePreview();
      }
      validateScenePreviewNumber(object.articulation.open_fraction);
      if (object.articulation.open_fraction < 0 || object.articulation.open_fraction > 1) {
        invalidHarnessScenePreview();
      }
      if ((object.articulation.state === 'closed' && object.articulation.open_fraction !== 0)
        || (object.articulation.state === 'open' && object.articulation.open_fraction !== 1)
        || (object.articulation.state === 'partially_open'
          && (object.articulation.open_fraction <= 0
            || object.articulation.open_fraction >= 1))) invalidHarnessScenePreview();
    }
  });
  const relationKinds = ['on_table', 'on_top_of', 'inside', 'left_of', 'right_of',
    'front_of', 'behind', 'near', 'distance_at_least'];
  if (!Array.isArray(scene.relations)
    || !scene.relations.length
    || scene.relations.length > 64) invalidHarnessScenePreview();
  const supportSources = new Set();
  const supportEdges = [];
  const horizontalEdges = [];
  const verticalEdges = [];
  const relationKeys = new Set();
  const distanceBounds = new Map();
  scene.relations.forEach((relation) => {
    if (!hasExactKeys(
      relation,
      ['max_distance_m', 'min_distance_m', 'relation', 'source', 'target'],
    ) || !relationKinds.includes(relation.relation)) invalidHarnessScenePreview();
    validateScenePreviewString(relation.source, {
      maximum: 64,
      pattern: scenePreviewObjectIdPattern,
    });
    validateScenePreviewString(relation.target, {
      maximum: 64,
      pattern: scenePreviewTargetPattern,
    });
    validateScenePreviewNumber(relation.max_distance_m, { nullable: true });
    validateScenePreviewNumber(relation.min_distance_m, { nullable: true });
    if (!objectIds.has(relation.source)
      || (relation.target !== 'table' && !objectIds.has(relation.target))
      || relation.source === relation.target
      || (relation.max_distance_m !== null
        && (relation.max_distance_m <= 0 || relation.max_distance_m > 1))
      || (relation.min_distance_m !== null
        && (relation.min_distance_m <= 0 || relation.min_distance_m > 1))) {
      invalidHarnessScenePreview();
    }
    const relationKey = JSON.stringify([
      relation.relation,
      relation.source,
      relation.target,
      relation.max_distance_m,
      relation.min_distance_m,
    ]);
    if (relationKeys.has(relationKey)) invalidHarnessScenePreview();
    relationKeys.add(relationKey);
    if (relation.relation === 'on_table') {
      if (relation.target !== 'table'
        || relation.max_distance_m !== null
        || relation.min_distance_m !== null) invalidHarnessScenePreview();
    } else if (relation.target === 'table') {
      invalidHarnessScenePreview();
    }
    if (relation.relation === 'near') {
      if (relation.min_distance_m !== null) invalidHarnessScenePreview();
    } else if (relation.max_distance_m !== null) invalidHarnessScenePreview();
    if (relation.relation === 'distance_at_least') {
      if (relation.min_distance_m === null) invalidHarnessScenePreview();
    } else if (relation.min_distance_m !== null) invalidHarnessScenePreview();
    if (['on_table', 'on_top_of', 'inside'].includes(relation.relation)) {
      if (supportSources.has(relation.source)) invalidHarnessScenePreview();
      supportSources.add(relation.source);
      if (relation.target !== 'table') supportEdges.push([relation.source, relation.target]);
    } else if (relation.relation === 'left_of') {
      horizontalEdges.push([relation.source, relation.target]);
    } else if (relation.relation === 'right_of') {
      horizontalEdges.push([relation.target, relation.source]);
    } else if (relation.relation === 'behind') {
      verticalEdges.push([relation.source, relation.target]);
    } else if (relation.relation === 'front_of') {
      verticalEdges.push([relation.target, relation.source]);
    } else {
      const pair = [relation.source, relation.target].sort().join('\u0000');
      const bounds = distanceBounds.get(pair) || {};
      if (relation.relation === 'near') bounds.maximum = relation.max_distance_m ?? 0.18;
      else bounds.minimum = relation.min_distance_m;
      distanceBounds.set(pair, bounds);
    }
  });
  if (supportSources.size !== objectIds.size
    || scenePreviewHasCycle(objectIds, supportEdges)
    || scenePreviewHasCycle(objectIds, horizontalEdges)
    || scenePreviewHasCycle(objectIds, verticalEdges)
    || [...distanceBounds.values()].some(
      (bounds) => (bounds.minimum ?? 0) > (bounds.maximum ?? Number.POSITIVE_INFINITY),
    )) invalidHarnessScenePreview();
  return scene;
}

function previewableSceneArtifact(audit) {
  if (audit.run.status !== 'succeeded' || audit.invocation.status !== 'bound') return null;
  const candidates = audit.artifacts.filter((artifact) => (
    artifact.name === 'scene_spec'
      && artifact.media_type === 'application/json'
      && artifact.schema_version === 'robotwin.scene_spec.v1'
      && artifact.bindings.length === 1
      && artifact.bindings[0].direction === 'output'
      && artifact.bindings[0].role === 'scene_spec'
      && BigInt(artifact.bytes) <= scenePreviewArtifactMaximumBytes
  ));
  return candidates.length === 1 ? candidates[0] : null;
}

function previewableStaticValidationArtifact(audit) {
  if (audit.run.status !== 'succeeded' || audit.invocation.status !== 'bound') return null;
  const candidates = audit.artifacts.filter((artifact) => (
    artifact.media_type === 'application/json'
      && artifact.schema_version === 'robotwin.scene_validation.v1'
      && artifact.bindings.length === 1
      && artifact.bindings[0].direction === 'output'
      && artifact.bindings[0].role === 'static_validation'
      && BigInt(artifact.bytes) <= staticValidationArtifactMaximumBytes
  ));
  return candidates.length === 1 ? candidates[0] : null;
}

function validateHarnessScenePreview(preview, authority) {
  if (!hasExactKeys(preview, ['artifact', 'run', 'scene', 'schema_version'])
    || preview.schema_version !== 'harness.workbench_compile_scene_preview.v1'
    || !hasExactKeys(
      preview.run,
      ['event_count', 'invocation_digest', 'run_id', 'terminal_event_id'],
    )
    || preview.run.run_id !== authority.run_id
    || preview.run.invocation_digest !== authority.invocation_digest
    || preview.run.event_count !== authority.event_count
    || preview.run.terminal_event_id !== authority.terminal_event_id) {
    invalidHarnessScenePreview();
  }
  try {
    validateHarnessAuditArtifact(preview.artifact);
  } catch (_error) {
    invalidHarnessScenePreview();
  }
  const artifact = authority.artifact;
  if (preview.artifact.name !== artifact.name
    || preview.artifact.media_type !== artifact.media_type
    || preview.artifact.schema_version !== artifact.schema_version
    || preview.artifact.sha256 !== artifact.sha256
    || preview.artifact.bytes !== artifact.bytes
    || preview.artifact.bindings.length !== 1
    || preview.artifact.bindings[0].direction !== 'output'
    || preview.artifact.bindings[0].role !== 'scene_spec') invalidHarnessScenePreview();
  validateSceneProjection(preview.scene);
  return preview;
}

async function readBoundedScenePreviewResponse(response) {
  const contentType = response.headers.get('Content-Type')?.split(';', 1)[0].trim().toLowerCase();
  if (contentType !== 'application/json' || !response.body) invalidHarnessScenePreview();
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > scenePreviewResponseMaximumBytes) {
      await reader.cancel();
      invalidHarnessScenePreview();
    }
    chunks.push(value);
  }
  const payload = new Uint8Array(length);
  let offset = 0;
  chunks.forEach((chunk) => {
    payload.set(chunk, offset);
    offset += chunk.byteLength;
  });
  if (payload.length >= 3 && payload[0] === 0xef && payload[1] === 0xbb && payload[2] === 0xbf) {
    invalidHarnessScenePreview();
  }
  let text;
  try {
    text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(payload);
  } catch (_error) {
    invalidHarnessScenePreview();
  }
  let value;
  try {
    value = JSON.parse(text);
  } catch (_error) {
    invalidHarnessScenePreview();
  }
  if (!response.ok) invalidHarnessScenePreview();
  return value;
}

function validateHarnessStaticValidationPreview(preview, authority) {
  if (!hasExactKeys(preview, ['artifact', 'run', 'schema_version', 'validation'])
    || preview.schema_version
      !== 'harness.workbench_compile_static_validation_preview.v1'
    || !hasExactKeys(
      preview.run,
      ['event_count', 'invocation_digest', 'run_id', 'terminal_event_id'],
    )
    || preview.run.run_id !== authority.run_id
    || preview.run.invocation_digest !== authority.invocation_digest
    || preview.run.event_count !== authority.event_count
    || preview.run.terminal_event_id !== authority.terminal_event_id) {
    invalidHarnessStaticValidationPreview();
  }
  try {
    validateHarnessAuditArtifact(preview.artifact);
  } catch (_error) {
    invalidHarnessStaticValidationPreview();
  }
  const artifact = authority.staticValidationArtifact;
  if (!artifact
    || preview.artifact.name !== artifact.name
    || preview.artifact.media_type !== artifact.media_type
    || preview.artifact.schema_version !== artifact.schema_version
    || preview.artifact.sha256 !== artifact.sha256
    || preview.artifact.bytes !== artifact.bytes
    || preview.artifact.bindings.length !== 1
    || preview.artifact.bindings[0].direction !== 'output'
    || preview.artifact.bindings[0].role !== 'static_validation') {
    invalidHarnessStaticValidationPreview();
  }
  const validation = preview.validation;
  if (!hasExactKeys(
    validation,
    ['checks', 'claim_scope', 'counts', 'mode', 'resolved_scene_sha256', 'scene_id', 'status'],
  )
    || validation.claim_scope !== 'committed_report_content_and_binding_only'
    || validation.mode !== 'compile_static_without_runtime_evidence'
    || typeof validation.scene_id !== 'string'
    || !scenePreviewSceneIdPattern.test(validation.scene_id)
    || typeof validation.resolved_scene_sha256 !== 'string'
    || !artifactDigestPattern.test(validation.resolved_scene_sha256)
    || !['fail', 'incomplete'].includes(validation.status)
    || !hasExactKeys(validation.counts, ['checks', 'fail', 'not_run', 'pass'])
    || !Array.isArray(validation.checks)
    || validation.checks.length < 1
    || validation.checks.length > staticValidationMaximumChecks) {
    invalidHarnessStaticValidationPreview();
  }
  const countValues = Object.values(validation.counts);
  if (countValues.some((value) => !Number.isSafeInteger(value) || value < 0)
    || validation.counts.checks !== validation.checks.length
    || validation.counts.not_run !== 1
    || validation.counts.pass + validation.counts.fail + validation.counts.not_run
      !== validation.counts.checks) invalidHarnessStaticValidationPreview();
  const statuses = new Set(['pass', 'fail', 'not_run']);
  const names = new Set();
  const observed = { pass: 0, fail: 0, not_run: 0 };
  validation.checks.forEach((check) => {
    if (!hasExactKeys(check, ['name', 'status'])
      || typeof check.name !== 'string'
      || !staticValidationCheckNamePattern.test(check.name)
      || names.has(check.name)
      || !statuses.has(check.status)) invalidHarnessStaticValidationPreview();
    names.add(check.name);
    observed[check.status] += 1;
  });
  if (observed.pass !== validation.counts.pass
    || observed.fail !== validation.counts.fail
    || observed.not_run !== validation.counts.not_run
    || validation.status !== (observed.fail ? 'fail' : 'incomplete')
    || validation.checks.find((check) => check.name === 'runtime_evidence')?.status
      !== 'not_run'
    || validation.checks.find((check) => check.name === 'package_manifest')?.status
      !== 'pass'
    || validation.checks.find((check) => check.name === 'resolved_only_roundtrip')?.status
      !== 'pass') {
    invalidHarnessStaticValidationPreview();
  }
  return preview;
}

async function readBoundedStaticValidationResponse(response) {
  const contentType = response.headers.get('Content-Type')?.split(';', 1)[0].trim().toLowerCase();
  if (contentType !== 'application/json' || !response.body) {
    invalidHarnessStaticValidationPreview();
  }
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > staticValidationResponseMaximumBytes) {
      await reader.cancel();
      invalidHarnessStaticValidationPreview();
    }
    chunks.push(value);
  }
  const payload = new Uint8Array(length);
  let offset = 0;
  chunks.forEach((chunk) => {
    payload.set(chunk, offset);
    offset += chunk.byteLength;
  });
  if (payload.length >= 3 && payload[0] === 0xef && payload[1] === 0xbb && payload[2] === 0xbf) {
    invalidHarnessStaticValidationPreview();
  }
  let text;
  try {
    text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(payload);
  } catch (_error) {
    invalidHarnessStaticValidationPreview();
  }
  let value;
  try {
    value = JSON.parse(text);
  } catch (_error) {
    invalidHarnessStaticValidationPreview();
  }
  if (!response.ok) invalidHarnessStaticValidationPreview();
  return value;
}

function appendScenePreviewSummary(summary, label, value) {
  const term = document.createElement('dt');
  term.textContent = label;
  const description = document.createElement('dd');
  description.textContent = value;
  summary.append(term, description);
}

function appendScenePreviewGroup(contents, title, values) {
  const group = document.createElement('div');
  group.className = 'scene-preview-group';
  const heading = document.createElement('h5');
  heading.textContent = title;
  const list = document.createElement('ol');
  list.className = 'scene-preview-list';
  values.forEach((value) => {
    const item = document.createElement('li');
    item.textContent = value;
    list.appendChild(item);
  });
  group.append(heading, list);
  contents.appendChild(group);
}

function renderHarnessScenePreview(preview) {
  const scene = preview.scene;
  const contents = $('#harness-scene-preview-content');
  contents.replaceChildren();
  const summary = document.createElement('dl');
  summary.className = 'scene-preview-summary';
  appendScenePreviewSummary(summary, 'Scene', scene.scene_id);
  appendScenePreviewSummary(summary, 'Language', scene.language);
  appendScenePreviewSummary(summary, 'Seed', String(scene.seed));
  appendScenePreviewSummary(summary, 'Unit', scene.unit);
  appendScenePreviewSummary(
    summary,
    'Frame',
    `${scene.frame.name} · ${scene.frame.x_axis}/${scene.frame.y_axis}/${scene.frame.z_axis} · ${scene.frame.handedness}`,
  );
  appendScenePreviewSummary(
    summary,
    'Workspace',
    `${scene.workspace.support_surface} · height ${scene.workspace.table_height_m} m · x [${scene.workspace.x_bounds_m.join(', ')}] · y [${scene.workspace.y_bounds_m.join(', ')}]`,
  );
  contents.appendChild(summary);
  appendScenePreviewGroup(
    contents,
    `Objects · ${scene.objects.length}`,
    scene.objects.map((object) => {
      const fields = [object.object_id, object.category];
      if (object.color !== null) fields.push(object.color);
      if (object.material !== null) fields.push(object.material);
      fields.push(object.region);
      if (object.articulation !== null) {
        fields.push(
          `${object.articulation.state} ${object.articulation.open_fraction} · ${object.articulation.joint_selector}`,
        );
      }
      return fields.join(' · ');
    }),
  );
  appendScenePreviewGroup(
    contents,
    `Relations · ${scene.relations.length}`,
    scene.relations.map((relation) => {
      const distances = [];
      if (relation.max_distance_m !== null) distances.push(`max ${relation.max_distance_m} m`);
      if (relation.min_distance_m !== null) distances.push(`min ${relation.min_distance_m} m`);
      return `${relation.source} ${relation.relation} ${relation.target}${distances.length ? ` · ${distances.join(' · ')}` : ''}`;
    }),
  );
}

function renderHarnessStaticValidationPreview(preview) {
  const validation = preview.validation;
  const summary = $('#harness-static-validation-summary');
  const checks = $('#harness-static-validation-checks');
  summary.replaceChildren();
  checks.replaceChildren();
  appendScenePreviewSummary(summary, 'Scene', validation.scene_id);
  appendScenePreviewSummary(summary, 'Status', validation.status);
  appendScenePreviewSummary(summary, 'Checks', String(validation.counts.checks));
  appendScenePreviewSummary(summary, 'Pass', String(validation.counts.pass));
  appendScenePreviewSummary(summary, 'Fail', String(validation.counts.fail));
  appendScenePreviewSummary(summary, 'Not run', String(validation.counts.not_run));
  appendScenePreviewSummary(summary, 'Resolved SHA-256', validation.resolved_scene_sha256);
  const statusLabels = {
    pass: '通过',
    fail: '失败',
    not_run: '未运行',
  };
  validation.checks.forEach((check) => {
    const item = document.createElement('li');
    const name = document.createElement('strong');
    name.textContent = check.name;
    const status = document.createElement('span');
    status.className = `static-validation-status ${check.status}`;
    status.textContent = statusLabels[check.status];
    item.append(name, status);
    checks.appendChild(item);
  });
}

function scenePreviewAuthorityIsCurrent(authority) {
  return state.harnessVerifiedAudit === authority
    && state.harnessRunId === authority.run_id
    && state.harnessCursor === authority.terminal_event_id
    && state.harnessEvents.length === authority.event_count;
}

function staticValidationAuthorityIsCurrent(authority) {
  return state.harnessVerifiedAudit === authority
    && authority.staticValidationArtifact !== null
    && state.harnessRunId === authority.run_id
    && state.harnessCursor === authority.terminal_event_id
    && state.harnessEvents.length === authority.event_count;
}

async function loadHarnessScenePreview() {
  const authority = state.harnessVerifiedAudit;
  const button = $('#harness-scene-preview-button');
  if (!authority || button.hidden || !scenePreviewAuthorityIsCurrent(authority)) return;
  resetHarnessStaticValidationPreview({ forgetAudit: false });
  resetHarnessScenePreview({ forgetAudit: false, message: '正在读取并验证场景结构…' });
  const generation = state.harnessScenePreviewGeneration;
  const controller = new AbortController();
  state.harnessScenePreviewController = controller;
  button.disabled = true;
  $('#harness-scene-preview-panel').setAttribute('aria-busy', 'true');
  try {
    const response = await fetch(
      `/api/harness/compile-runs/${encodeURIComponent(authority.run_id)}/scene-preview`,
      {
        method: 'GET',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal,
      },
    );
    const preview = await readBoundedScenePreviewResponse(response);
    if (generation !== state.harnessScenePreviewGeneration
      || controller !== state.harnessScenePreviewController
      || !scenePreviewAuthorityIsCurrent(authority)) return;
    validateHarnessScenePreview(preview, authority);
    if (generation !== state.harnessScenePreviewGeneration
      || !scenePreviewAuthorityIsCurrent(authority)) return;
    state.harnessScenePreviewController = null;
    button.disabled = false;
    button.setAttribute('aria-expanded', 'true');
    const panel = $('#harness-scene-preview-panel');
    panel.setAttribute('aria-busy', 'false');
    renderHarnessScenePreview(preview);
    panel.hidden = false;
    $('#harness-scene-preview-message').textContent = '场景结构已验证；内容按字段投影显示。';
    $('#harness-scene-preview-title').focus();
  } catch (_error) {
    if (generation !== state.harnessScenePreviewGeneration
      || controller !== state.harnessScenePreviewController) return;
    state.harnessScenePreviewController = null;
    button.disabled = false;
    button.setAttribute('aria-expanded', 'false');
    const panel = $('#harness-scene-preview-panel');
    panel.hidden = true;
    panel.setAttribute('aria-busy', 'false');
    $('#harness-scene-preview-content').replaceChildren();
    $('#harness-scene-preview-message').textContent = '场景结构预览无法验证。';
  }
}

async function loadHarnessStaticValidationPreview() {
  const authority = state.harnessVerifiedAudit;
  const button = $('#harness-static-validation-button');
  if (!authority || button.hidden || !staticValidationAuthorityIsCurrent(authority)) return;
  resetHarnessScenePreview({ forgetAudit: false });
  resetHarnessStaticValidationPreview({
    forgetAudit: false,
    message: '正在读取并核对编译期检查…',
  });
  const generation = state.harnessStaticValidationGeneration;
  const controller = new AbortController();
  state.harnessStaticValidationController = controller;
  button.disabled = true;
  $('#harness-static-validation-panel').setAttribute('aria-busy', 'true');
  try {
    const response = await fetch(
      `/api/harness/compile-runs/${encodeURIComponent(authority.run_id)}/static-validation-preview`,
      {
        method: 'GET',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal,
      },
    );
    const preview = await readBoundedStaticValidationResponse(response);
    if (generation !== state.harnessStaticValidationGeneration
      || controller !== state.harnessStaticValidationController
      || !staticValidationAuthorityIsCurrent(authority)) return;
    validateHarnessStaticValidationPreview(preview, authority);
    if (generation !== state.harnessStaticValidationGeneration
      || !staticValidationAuthorityIsCurrent(authority)) return;
    state.harnessStaticValidationController = null;
    button.disabled = false;
    button.setAttribute('aria-expanded', 'true');
    const panel = $('#harness-static-validation-panel');
    panel.setAttribute('aria-busy', 'false');
    renderHarnessStaticValidationPreview(preview);
    panel.hidden = false;
    const outcome = preview.validation.status === 'fail'
      ? `检测到 ${preview.validation.counts.fail} 项失败；`
      : '报告状态为 incomplete；';
    $('#harness-static-validation-message').textContent = `报告内容身份、结构及与当前审计的绑定已核对；${outcome}validator 未重跑，未执行物理回放，不作发布判断。`;
    $('#harness-static-validation-title').focus();
  } catch (_error) {
    if (generation !== state.harnessStaticValidationGeneration
      || controller !== state.harnessStaticValidationController) return;
    state.harnessStaticValidationController = null;
    button.disabled = false;
    button.setAttribute('aria-expanded', 'false');
    const panel = $('#harness-static-validation-panel');
    panel.hidden = true;
    panel.setAttribute('aria-busy', 'false');
    $('#harness-static-validation-summary').replaceChildren();
    $('#harness-static-validation-checks').replaceChildren();
    $('#harness-static-validation-message').textContent = '编译期检查预览无法验证。';
  }
}

function appendAuditSummary(summary, label, value) {
  const term = document.createElement('dt');
  term.textContent = label;
  const description = document.createElement('dd');
  description.textContent = value;
  summary.append(term, description);
}

function renderHarnessAudit(audit) {
  resetHarnessScenePreview();
  resetHarnessStaticValidationPreview();
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
  const sceneArtifact = previewableSceneArtifact(audit);
  const staticValidationArtifact = previewableStaticValidationArtifact(audit);
  if (sceneArtifact || staticValidationArtifact) {
    state.harnessVerifiedAudit = {
      run_id: audit.run.run_id,
      invocation_digest: audit.invocation.digest,
      event_count: audit.run.event_count,
      terminal_event_id: audit.run.terminal_event_id,
      artifact: sceneArtifact ? {
        ...sceneArtifact,
        bindings: sceneArtifact.bindings.map((binding) => ({ ...binding })),
      } : null,
      staticValidationArtifact: staticValidationArtifact ? {
        ...staticValidationArtifact,
        bindings: staticValidationArtifact.bindings.map((binding) => ({ ...binding })),
      } : null,
    };
  }
  if (sceneArtifact) {
    const button = $('#harness-scene-preview-button');
    button.hidden = false;
    $('#harness-scene-preview-message').textContent = '可按需读取已提交的 SceneSpec 结构。';
  }
  if (staticValidationArtifact) {
    const button = $('#harness-static-validation-button');
    button.hidden = false;
    $('#harness-static-validation-message').textContent = '可按需读取已提交的编译期检查报告。';
  }
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
  resetHarnessScenePreview();
  resetHarnessStaticValidationPreview();
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
  harnessRunBoard.render({
    events: state.harnessEvents,
    selectedRunId: state.harnessRunId,
  });
}

async function loadHarnessEvents() {
  clearTimeout(state.harnessPollTimer);
  const requestGeneration = ++state.harnessRequestGeneration;
  const requestedRunId = state.harnessRunId;
  const previousAuthorityKey = `${state.harnessCursor}:${state.harnessEvents.length}`;
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
    if (previousAuthorityKey !== `${state.harnessCursor}:${state.harnessEvents.length}`) {
      resetHarnessAudit('committed journal 已变化；正在重新对账。');
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
$('#harness-scene-preview-button').addEventListener('click', loadHarnessScenePreview);
$('#harness-scene-preview-close').addEventListener('click', () => {
  resetHarnessScenePreview({ forgetAudit: false, returnFocus: true });
});
$('#harness-static-validation-button').addEventListener(
  'click',
  loadHarnessStaticValidationPreview,
);
$('#harness-static-validation-close').addEventListener('click', () => {
  resetHarnessStaticValidationPreview({ forgetAudit: false, returnFocus: true });
});
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
