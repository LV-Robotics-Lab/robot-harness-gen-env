(function installHarnessRunBoard(global) {
  'use strict';

  const lanes = ['compile', 'replay', 'validate', 'other'];

  function laneFor(skillId) {
    if (skillId === 'text2env.compile') return 'compile';
    if (skillId === 'text2env.replay') return 'replay';
    if (skillId === 'text2env.validate') return 'validate';
    return 'other';
  }

  function invalidVisibleHistory() {
    const error = new TypeError(
      'HarnessRunBoard run identity changed inside the visible journal',
    );
    error.code = 'harness_event_page_invalid';
    throw error;
  }

  function mount({ root, onInspect }) {
    if (!(root instanceof global.HTMLElement) || typeof onInspect !== 'function') {
      throw new TypeError('HarnessRunBoard requires a root element and onInspect callback');
    }
    const lists = new Map(lanes.map((lane) => [
      lane,
      root.querySelector(`[data-run-lane-list="${lane}"]`),
    ]));
    if ([...lists.values()].some((list) => !(list instanceof global.HTMLOListElement))) {
      throw new TypeError('HarnessRunBoard lane markup is incomplete');
    }
    let currentProjection = null;
    let pendingFocusRunId = null;
    const runNodes = new Map();

    function clear() {
      lists.forEach((list) => list.replaceChildren());
      runNodes.clear();
      currentProjection = null;
      pendingFocusRunId = null;
    }

    function render({ events, selectedRunId }) {
      if (!Array.isArray(events) || typeof selectedRunId !== 'string') {
        throw new TypeError('HarnessRunBoard render input is invalid');
      }
      const runs = new Map();
      events.forEach((envelope) => {
        const existing = runs.get(envelope.run_id);
        if (existing
          && (existing.skillId !== envelope.skill_id
            || existing.skillVersion !== envelope.skill_version)) {
          invalidVisibleHistory();
        }
        runs.set(envelope.run_id, {
          runId: envelope.run_id,
          skillId: envelope.skill_id,
          skillVersion: envelope.skill_version,
          latest: envelope,
          visibleEventCount: (existing?.visibleEventCount || 0) + 1,
        });
      });

      const orderedRuns = [...runs.values()]
        .sort((left, right) => {
          const leftId = BigInt(left.latest.event_id);
          const rightId = BigInt(right.latest.event_id);
          if (leftId === rightId) return left.runId.localeCompare(right.runId);
          return leftId > rightId ? -1 : 1;
        });
      const nextProjection = JSON.stringify(orderedRuns.map((run) => [
        laneFor(run.skillId),
        run.runId,
        run.skillId,
        run.skillVersion,
        run.latest.event_id,
        run.latest.event.stage,
        run.latest.event.to_status,
        run.visibleEventCount,
        run.runId === selectedRunId,
      ]));
      if (nextProjection === currentProjection) return;

      const activeElement = global.document.activeElement;
      if (root.contains(activeElement) && activeElement?.dataset?.runId) {
        pendingFocusRunId = activeElement.dataset.runId;
      }
      const nextRunIds = new Set(orderedRuns.map((run) => run.runId));
      runNodes.forEach((node, runId) => {
        if (!nextRunIds.has(runId)) {
          node.item.remove();
          runNodes.delete(runId);
        }
      });
      lists.forEach((list) => {
        list.querySelectorAll('.run-activity-empty').forEach((empty) => empty.remove());
      });
      orderedRuns
        .forEach((run) => {
          let node = runNodes.get(run.runId);
          if (!node) {
            const item = document.createElement('li');
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'run-activity-card';
            const skill = document.createElement('strong');
            const stage = document.createElement('span');
            const facts = document.createElement('small');
            button.append(skill, stage, facts);
            item.appendChild(button);
            node = { item, button, skill, stage, facts };
            runNodes.set(run.runId, node);
            button.addEventListener('click', () => onInspect(run.runId));
          }
          const { item, button, skill, stage, facts } = node;
          button.dataset.runId = run.runId;
          button.dataset.latestEventId = run.latest.event_id;
          button.dataset.visibleEventCount = String(run.visibleEventCount);
          if (run.runId === selectedRunId) button.setAttribute('aria-current', 'true');
          else button.removeAttribute('aria-current');
          skill.textContent = `${run.skillId}@${run.skillVersion}`;
          stage.textContent = run.latest.event.stage;
          facts.textContent = `${run.latest.event.to_status} · ${run.visibleEventCount} 个可见事件 · #${run.latest.event_id}`;
          lists.get(laneFor(run.skillId)).appendChild(item);
        });

      lists.forEach((list) => {
        if (list.childElementCount) return;
        const empty = document.createElement('li');
        empty.className = 'run-activity-empty';
        empty.textContent = '暂无可见事件';
        list.appendChild(empty);
      });
      currentProjection = nextProjection;
      const focusNode = pendingFocusRunId ? runNodes.get(pendingFocusRunId) : null;
      if (focusNode) {
        if (global.document.activeElement === global.document.body) {
          focusNode.button.focus();
        }
        pendingFocusRunId = null;
      } else if (pendingFocusRunId !== selectedRunId) {
        pendingFocusRunId = null;
      }
    }

    return Object.freeze({ clear, render });
  }

  global.HarnessRunBoard = Object.freeze({ mount });
}(window));
