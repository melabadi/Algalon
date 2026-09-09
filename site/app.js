const scenarioNames = ['pessimistic', 'base', 'optimistic'];
let modelConfig = null;
let activeScenario = 'base';

function nestedValue(object, path) {
  return path.split('.').reduce((value, key) => value?.[key], object);
}

function formatScenarioValue(path, scenario) {
  if (path === 'coding.summary') {
    return `${Math.round(scenario.coding.manualEntryFraction * 100)}% @ ${scenario.coding.wordsPerMinute} words/min`;
  }
  const value = nestedValue(scenario, path);
  if (path.endsWith('relevantTokenFraction')) return `${Math.round(value * 100)}%`;
  if (path.endsWith('reasoningTokenWeight')) return `${Math.round((value ?? 0) * 100)}%`;
  if (path.endsWith('tokensPerMinute')) return `${value} tokens/min`;
  if (path.endsWith('interactionMinutesPerTool')) return `${value} min/tool`;
  if (path === 'unclassifiedManualMultiplier') return `${value}×`;
  return String(value ?? '—');
}

function renderScenario(name) {
  if (!modelConfig || !scenarioNames.includes(name)) return;
  activeScenario = name;
  const scenario = modelConfig.benchmark.scenarios[name];
  document.querySelectorAll('[data-scenario]').forEach((button) => {
    const selected = button.dataset.scenario === name;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  document.querySelectorAll('[data-value]').forEach((cell) => {
    cell.textContent = formatScenarioValue(cell.dataset.value, scenario);
  });
  const label = name[0].toUpperCase() + name.slice(1);
  document.querySelector('#scenario-status').textContent =
    `${label} assumptions from config/value-model.example.json. Observed evidence does not change.`;
}

function evidenceClassLabel(value) {
  return {
    controlled_experiment: 'Controlled experiment',
    literature_benchmark: 'Literature benchmark',
    official_statistic: 'Official statistic',
    survey_context: 'Survey context',
    local_measurement: 'Local measurement',
    other: 'Other evidence'
  }[value] ?? value;
}

function strongestSupport(source) {
  const levels = Object.values(source.supportLevels ?? {});
  if (levels.includes('direct')) return 'direct';
  if (levels.includes('proxy')) return 'proxy';
  return 'context';
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderSources(sources) {
  const register = document.querySelector('#source-register');
  register.replaceChildren();
  sources.forEach((source, index) => {
    const entry = element('details', 'source-entry');
    const summary = element('summary');
    summary.append(element('span', 'source-index', `[${index + 1}]`));
    const title = element('span', 'source-title');
    title.append(element('strong', '', source.title));
    title.append(element('small', '', `${source.publisher} · ${source.publishedAt}`));
    summary.append(title);
    summary.append(element('span', `support-badge support-${strongestSupport(source)}`, evidenceClassLabel(source.evidenceClass)));
    entry.append(summary);

    const body = element('div', 'source-body');
    const finding = element('p');
    finding.append(element('strong', '', 'Finding: '), document.createTextNode(source.finding));
    const limitation = element('p');
    limitation.append(element('strong', '', 'Limit: '), document.createTextNode(source.limitation));
    body.append(finding, limitation);

    const mappings = element('div', 'source-mapping');
    source.appliesTo.forEach((target) => {
      const support = source.supportLevels?.[target] ?? 'context';
      mappings.append(element('span', `support-badge support-${support}`, `${support}: ${target}`));
    });
    const link = element('a', 'source-link', 'Open primary source ↗');
    link.href = source.url;
    link.target = '_blank';
    link.rel = 'noreferrer';
    mappings.append(link);
    body.append(mappings);
    entry.append(body);
    register.append(entry);
  });
}

async function loadModelConfig() {
  try {
    const response = await fetch('data/value-model.example.json');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    modelConfig = await response.json();
    renderScenario(activeScenario);
    renderSources(modelConfig.benchmark.calibrationSources ?? []);
  } catch (error) {
    document.querySelector('#scenario-status').textContent = 'The checked-in assumptions could not be loaded.';
    document.querySelector('#source-register').replaceChildren(
      element('p', 'loading-copy', 'The checked-in evidence register could not be loaded. Open the full methodology in the repository.')
    );
  }
}

function formatMinutes(value) {
  return `${value.toFixed(1)} min`;
}

function updateBreakEven() {
  const aiMinutes = Number(document.querySelector('#ai-minutes').value);
  const aiCost = Number(document.querySelector('#ai-cost').value);
  const laborRate = Number(document.querySelector('#labor-rate').value);
  const capacity = Number(document.querySelector('#capacity').value);
  const error = document.querySelector('#calculator-error');
  if (aiMinutes <= 0 || aiCost < 0 || laborRate <= 0 || capacity <= 0 || capacity > 1) {
    error.textContent = 'Use positive time and labor values, nonnegative AI cost, and capacity between 0 and 1.';
    return;
  }
  error.textContent = '';
  const valuePerMinute = laborRate / 60 * capacity;
  const additionalMinutes = aiCost / valuePerMinute;
  const manualBaseline = aiMinutes + additionalMinutes;
  const timeReduction = manualBaseline > 0 ? additionalMinutes / manualBaseline * 100 : 0;
  document.querySelector('#value-per-minute').textContent = `$${valuePerMinute.toFixed(2)}`;
  document.querySelector('#additional-minutes').textContent = formatMinutes(additionalMinutes);
  document.querySelector('#manual-baseline').textContent = formatMinutes(manualBaseline);
  document.querySelector('#time-reduction').textContent = `${timeReduction.toFixed(1)}%`;
}

document.querySelectorAll('[data-scenario]').forEach((button) => {
  button.addEventListener('click', () => renderScenario(button.dataset.scenario));
});
document.querySelector('#break-even-form').addEventListener('input', updateBreakEven);
document.querySelector('#break-even-form').addEventListener('submit', (event) => event.preventDefault());

updateBreakEven();
loadModelConfig();