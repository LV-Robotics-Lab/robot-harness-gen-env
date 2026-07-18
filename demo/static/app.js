const state = {
  activeJob: null,
  pollTimer: null,
  mediaKey: null,
  artifactKey: null,
};

const $ = (selector) => document.querySelector(selector);
const artifactUrl = (job, path) => `/api/jobs/${job.job_id}/artifacts/${path.split('/').map(encodeURIComponent).join('/')}`;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(value.error?.message || `HTTP ${response.status}`);
  return value;
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { 'aria-hidden': 'true' } });
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

$('#example-select').addEventListener('change', (event) => {
  if (event.target.value) $('#prompt').value = event.target.value;
});
$('#refresh-button').addEventListener('click', () => state.activeJob && loadJob(state.activeJob.job_id));
$('#open-result-button').addEventListener('click', () => {
  if (state.activeJob) window.open(`/?job=${encodeURIComponent(state.activeJob.job_id)}`, '_blank', 'noopener');
});

async function start() {
  refreshIcons();
  await Promise.all([checkHealth(), loadHistory()]);
  const jobId = new URL(window.location.href).searchParams.get('job');
  if (jobId) await loadJob(jobId);
}

start();
