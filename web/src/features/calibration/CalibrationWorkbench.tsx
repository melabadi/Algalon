import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { Copy, ExternalLink, Plus, RotateCcw, Save, Trash2 } from 'lucide-react';
import valueModelExample from '../../../../config/value-model.example.json';
import { Badge } from '../../components/ui';
import { Formula } from '../methodology/Formula';
import { messages } from '../../i18n';
import { scenarioLabel } from '../../lib/format';
import type { BenchmarkScenario, CalibrationSource, CalibrationSupportLevel, Methodology, Scenario } from '../../types';
import {
  activityLabel,
  calibrationError,
  calibrationSourcesWithBundledEvidence,
  scenarios,
  tokenPhases,
  type CalibrationDraft,
  type SavedCalibration,
  type ScenarioSelection,
  type TokenPhase
} from './model';

function evidenceClassLabel(value: CalibrationSource['evidenceClass']): string {
  return messages.evidenceClasses[value];
}

function CalibrationNumberInput({ label, value, min, max, step, onChange }: {
  label: string;
  value: number;
  min: number;
  max?: number;
  step: number;
  onChange: (value: number) => void;
}) {
  const [rawValue, setRawValue] = useState(String(value));
  useEffect(() => setRawValue(String(value)), [value]);
  return <input
    type="number"
    aria-label={label}
    value={rawValue}
    min={min}
    max={max}
    step={step}
    onChange={(event) => {
      setRawValue(event.currentTarget.value);
      const parsed = event.currentTarget.valueAsNumber;
      if (Number.isFinite(parsed)) onChange(parsed);
    }}
    onBlur={() => { if (!rawValue) setRawValue(String(value)); }}
  />;
}

export function CalibrationWorkbench({ methodology, scenario, savedCalibrations, setSavedCalibrations, activeCalibrationId, setActiveCalibrationId }: {
  methodology: Methodology;
  scenario: ScenarioSelection;
  savedCalibrations: SavedCalibration[];
  setSavedCalibrations: Dispatch<SetStateAction<SavedCalibration[]>>;
  activeCalibrationId: string | null;
  setActiveCalibrationId: Dispatch<SetStateAction<string | null>>;
}) {
  const benchmark = methodology.config.benchmark;
  if (methodology.config.loadedHourlyRateUsd == null || !benchmark ||
    benchmark.capacityRealization == null || benchmark.typingWordsPerMinute == null ||
    benchmark.charactersPerWord == null || !benchmark.scenarios) {
    return <p className="muted">Calibration workspace unavailable until the complete benchmark configuration is loaded.</p>;
  }
  return <CalibrationEditor
    scenario={scenario}
    loadedHourlyRateUsd={methodology.config.loadedHourlyRateUsd}
    benchmark={benchmark}
    capacityRealization={benchmark.capacityRealization}
    typingWordsPerMinute={benchmark.typingWordsPerMinute}
    charactersPerWord={benchmark.charactersPerWord}
    configuredScenarios={benchmark.scenarios}
    configuredSources={calibrationSourcesWithBundledEvidence(benchmark.calibrationSources)}
    savedCalibrations={savedCalibrations}
    setSavedCalibrations={setSavedCalibrations}
    activeCalibrationId={activeCalibrationId}
    setActiveCalibrationId={setActiveCalibrationId}
  />;
}

function CalibrationEditor({ scenario, loadedHourlyRateUsd, benchmark, capacityRealization, typingWordsPerMinute, charactersPerWord, configuredScenarios, configuredSources, savedCalibrations, setSavedCalibrations, activeCalibrationId, setActiveCalibrationId }: {
  scenario: ScenarioSelection;
  loadedHourlyRateUsd: number;
  benchmark: NonNullable<Methodology['config']['benchmark']>;
  capacityRealization: number;
  typingWordsPerMinute: number;
  charactersPerWord: number;
  configuredScenarios: Record<Scenario, BenchmarkScenario>;
  configuredSources: CalibrationSource[];
  savedCalibrations: SavedCalibration[];
  setSavedCalibrations: Dispatch<SetStateAction<SavedCalibration[]>>;
  activeCalibrationId: string | null;
  setActiveCalibrationId: Dispatch<SetStateAction<string | null>>;
}) {
  const presetScenarios = benchmark.presetScenarios ?? configuredScenarios;
  const createDraft = (): CalibrationDraft => ({
    loadedHourlyRateUsd,
    capacityRealization,
    capacityRealizationBand: benchmark.capacityRealizationBand,
    typingWordsPerMinute,
    charactersPerWord,
    scenarios: structuredClone(presetScenarios)
  });
  const [draft, setDraft] = useState<CalibrationDraft>(createDraft);
  const [customScenario, setCustomScenario] = useState<Scenario>('base');
  const [calibrationName, setCalibrationName] = useState('');
  const [profileStatus, setProfileStatus] = useState('');
  const [actionStatus, setActionStatus] = useState('');
  const editableScenario = scenario === 'custom' ? customScenario : scenario;
  const selected = draft.scenarios[editableScenario];
  const error = calibrationError(draft);

  useEffect(() => {
    if (!activeCalibrationId) {
      setDraft(createDraft());
      setCustomScenario('base');
      setCalibrationName('');
      return;
    }
    const active = savedCalibrations.find((calibration) => calibration.id === activeCalibrationId);
    if (!active) return;
    setDraft(structuredClone(active.draft));
    setCustomScenario(active.customScenario);
    setCalibrationName(active.name);
  }, [activeCalibrationId, savedCalibrations]);

  const updateDraft = (update: (previous: CalibrationDraft) => CalibrationDraft) => {
    setDraft(update);
    setProfileStatus('');
    setActionStatus('');
  };
  const updateGlobal = (key: 'loadedHourlyRateUsd' | 'capacityRealization' | 'typingWordsPerMinute' | 'charactersPerWord', value: number) => {
    updateDraft((previous) => ({ ...previous, [key]: value }));
  };
  const updateToken = (phase: TokenPhase, key: keyof BenchmarkScenario[TokenPhase], value: number) => {
    updateDraft((previous) => ({
      ...previous,
      scenarios: {
        ...previous.scenarios,
        [editableScenario]: {
          ...previous.scenarios[editableScenario],
          [phase]: { ...previous.scenarios[editableScenario][phase], [key]: value }
        }
      }
    }));
  };
  const updateCoding = (key: keyof BenchmarkScenario['coding'], value: number) => {
    updateDraft((previous) => ({
      ...previous,
      scenarios: {
        ...previous.scenarios,
        [editableScenario]: {
          ...previous.scenarios[editableScenario],
          coding: { ...previous.scenarios[editableScenario].coding, [key]: value }
        }
      }
    }));
  };

  const newFromPresets = () => {
    setDraft(createDraft());
    setCustomScenario('base');
    setActiveCalibrationId(null);
    setCalibrationName('');
    setProfileStatus('New custom calibration started from the preserved presets.');
  };

  const selectSavedCalibration = (id: string) => {
    if (!id) {
      newFromPresets();
      return;
    }
    const saved = savedCalibrations.find((calibration) => calibration.id === id);
    if (!saved) return;
    setDraft(structuredClone(saved.draft));
    setCustomScenario(saved.customScenario);
    setActiveCalibrationId(saved.id);
    setCalibrationName(saved.name);
    setProfileStatus(`Loaded “${saved.name}”.`);
  };

  const saveCalibration = () => {
    const name = calibrationName.trim();
    if (error) {
      setProfileStatus(error);
      return;
    }
    if (!name) {
      setProfileStatus('Enter a calibration name before saving.');
      return;
    }
    const duplicate = savedCalibrations.find((calibration) =>
      calibration.id !== activeCalibrationId && calibration.name.toLocaleLowerCase() === name.toLocaleLowerCase()
    );
    if (duplicate) {
      setProfileStatus('Calibration names must be unique.');
      return;
    }
    const id = activeCalibrationId ?? `calibration-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const saved: SavedCalibration = {
      id,
      name,
      updatedAt: new Date().toISOString(),
      customScenario,
      draft: structuredClone(draft)
    };
    setSavedCalibrations((previous) => activeCalibrationId
      ? previous.map((calibration) => calibration.id === activeCalibrationId ? saved : calibration)
      : [...previous, saved]
    );
    setActiveCalibrationId(id);
    setCalibrationName(name);
    setProfileStatus(`Saved “${name}” locally and applied it to data views. Preset values were not changed.`);
  };

  const deleteCalibration = () => {
    if (!activeCalibrationId) return;
    setSavedCalibrations((previous) => previous.filter((calibration) => calibration.id !== activeCalibrationId));
    newFromPresets();
    setProfileStatus('Saved calibration deleted. Preset values were not changed.');
  };

  const copyConfiguration = async () => {
    if (error) return;
    const fragment = {
      loadedHourlyRateUsd: draft.loadedHourlyRateUsd,
      benchmark: {
        ...benchmark,
        acknowledgedAssumptions: true,
        manualTimeModelSource: `Saved custom calibration${calibrationName.trim() ? ` “${calibrationName.trim()}”` : ''}; curated source coverage is recorded separately`,
        capacityRealization: draft.capacityRealization,
        typingWordsPerMinute: draft.typingWordsPerMinute,
        charactersPerWord: draft.charactersPerWord,
        calibrationSources: configuredSources,
        presetScenarios,
        scenarios: draft.scenarios
      }
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(fragment, null, 2));
      setActionStatus('Copied active values. The running worker and preserved presets remain unchanged until the fragment is deliberately applied.');
    } catch {
      setActionStatus('Clipboard access was unavailable.');
    }
  };

  return <div className="calibration-workbench">
    <div className="calibration-scope">
      <div><strong>Custom calibration</strong><span>Every custom profile starts from preserved presets. Saving a profile never overwrites pessimistic, base, or optimistic.</span></div>
      <Badge kind="modeled">{scenarioLabel(scenario)} inputs</Badge>
    </div>
    <div className="calibration-profile-bar">
      <label><span>Saved calibration</span><select aria-label="Saved calibration" value={activeCalibrationId ?? ''} onChange={(event) => selectSavedCalibration(event.currentTarget.value)}><option value="">Unsaved custom from presets</option>{savedCalibrations.map((calibration) => <option key={calibration.id} value={calibration.id}>{calibration.name}</option>)}</select></label>
      <label><span>Custom uses</span><select aria-label="Custom scenario branch" value={customScenario} onChange={(event) => { setCustomScenario(event.currentTarget.value as Scenario); setProfileStatus(''); }}>{scenarios.map((value) => <option key={value} value={value}>{scenarioLabel(value)} values</option>)}</select></label>
      <label className="calibration-name"><span>Calibration name</span><input aria-label="Calibration name" aria-describedby="calibration-profile-status" required value={calibrationName} placeholder="e.g. Backend bug fixes" onChange={(event) => { setCalibrationName(event.currentTarget.value); setProfileStatus(''); }} /></label>
      <button type="button" className="command-button primary-command" disabled={Boolean(error)} onClick={saveCalibration}><Save size={15} />Save calibration</button>
      <button type="button" className="command-button" onClick={newFromPresets}><Plus size={15} />New from presets</button>
      {activeCalibrationId && <button type="button" className="icon-command danger-command" aria-label="Delete saved calibration" title="Delete saved calibration" onClick={deleteCalibration}><Trash2 size={15} /></button>}
      {(error || profileStatus) && <span id="calibration-profile-status" className={`calibration-profile-status ${error || profileStatus.startsWith('Enter ') || profileStatus.endsWith('unique.') ? 'calibration-error' : 'muted'}`} role={error || profileStatus.startsWith('Enter ') || profileStatus.endsWith('unique.') ? 'alert' : 'status'}>{error ?? profileStatus}</span>}
    </div>
    <div className="calibration-global-grid">
      <label><span>Loaded labor rate</span><CalibrationNumberInput label="Loaded labor rate" value={draft.loadedHourlyRateUsd} min={0.01} step={1} onChange={(value) => updateGlobal('loadedHourlyRateUsd', value)} /><small>USD/hour</small></label>
      <label><span>Capacity realization</span><CalibrationNumberInput label="Capacity realization" value={draft.capacityRealization} min={0.01} max={1} step={0.05} onChange={(value) => updateGlobal('capacityRealization', value)} /><small>0–1</small></label>
      <label><span>Audit typing rate</span><CalibrationNumberInput label="Audit typing rate" value={draft.typingWordsPerMinute} min={1} step={1} onChange={(value) => updateGlobal('typingWordsPerMinute', value)} /><small>words/min</small></label>
      <label><span>Characters per word</span><CalibrationNumberInput label="Characters per word" value={draft.charactersPerWord} min={0.1} step={0.1} onChange={(value) => updateGlobal('charactersPerWord', value)} /><small>characters</small></label>
    </div>
    <div className="research-anchor-summary">
      <strong>Research-anchored shipped defaults</strong>
      <span>
        ${valueModelExample.loadedHourlyRateUsd}/hour from <a href="#calibration-source-6">BLS wages</a> plus <a href="#calibration-source-7">benefits</a>;
        {' '}{valueModelExample.benchmark.typingWordsPerMinute} WPM from the <a href="#calibration-source-5">typing study</a>;
        {' '}{valueModelExample.benchmark.scenarios.pessimistic.planning.interactionMinutesPerTool} min/tool for the lower branch from the <a href="#calibration-source-9">Copilot interaction study</a>.
        These are rounded proxies, not local measurements.
      </span>
    </div>
    <div className="calibration-table-wrap">
      <table className="calibration-input-table">
        <thead><tr><th>{scenarioLabel(scenario)} assumption</th>{tokenPhases.map((phase) => <th key={phase}>{activityLabel(phase)}</th>)}</tr></thead>
        <tbody>
          <tr><td>Relevant token fraction <Formula expression="alpha_P/R/V,s" /></td>{tokenPhases.map((phase) => <td key={phase}><CalibrationNumberInput label={`${scenarioLabel(scenario)} ${phase} relevant token fraction`} value={selected[phase].relevantTokenFraction} min={0} max={1} step={0.05} onChange={(value) => updateToken(phase, 'relevantTokenFraction', value)} /></td>)}</tr>
          <tr><td>Reasoning token weight <Formula expression="omega_P/V,s" /></td>{tokenPhases.map((phase) => phase === 'research'
            ? <td key={phase}><span className="muted">Not used</span></td>
            : <td key={phase}><CalibrationNumberInput label={`${scenarioLabel(scenario)} ${phase} reasoning token weight`} value={selected[phase].reasoningTokenWeight ?? 0} min={0} max={1} step={0.05} onChange={(value) => updateToken(phase, 'reasoningTokenWeight', value)} /></td>)}</tr>
          <tr><td>Human review rate <Formula expression="v_P/R/V,s" /></td>{tokenPhases.map((phase) => <td key={phase}><CalibrationNumberInput label={`${scenarioLabel(scenario)} ${phase} human review rate`} value={selected[phase].tokensPerMinute} min={1} step={25} onChange={(value) => updateToken(phase, 'tokensPerMinute', value)} /></td>)}</tr>
          <tr><td>Tool interaction overhead <Formula expression="tau_P/R/V,s" /></td>{tokenPhases.map((phase) => <td key={phase}><CalibrationNumberInput label={`${scenarioLabel(scenario)} ${phase} tool interaction overhead`} value={selected[phase].interactionMinutesPerTool} min={0} step={0.01} onChange={(value) => updateToken(phase, 'interactionMinutesPerTool', value)} /></td>)}</tr>
        </tbody>
      </table>
    </div>
    <div className="calibration-special-grid">
      <label><span>Manual source-entry fraction</span><CalibrationNumberInput label={`${scenarioLabel(scenario)} manual source-entry fraction`} value={selected.coding.manualEntryFraction} min={0} max={1} step={0.05} onChange={(value) => updateCoding('manualEntryFraction', value)} /><small>0–1</small></label>
      <label><span>Manual source-entry rate</span><CalibrationNumberInput label={`${scenarioLabel(scenario)} manual source-entry rate`} value={selected.coding.wordsPerMinute} min={1} step={1} onChange={(value) => updateCoding('wordsPerMinute', value)} /><small>words/min</small></label>
      <label><span>Unclassified multiplier</span><CalibrationNumberInput label={`${scenarioLabel(scenario)} unclassified multiplier`} value={selected.unclassifiedManualMultiplier} min={0.01} step={0.05} onChange={(value) => updateDraft((previous) => ({ ...previous, scenarios: { ...previous.scenarios, [editableScenario]: { ...previous.scenarios[editableScenario], unclassifiedManualMultiplier: value } } }))} /><small>multiplier</small></label>
    </div>
    <BaseInputEvidenceTable sources={configuredSources} base={presetScenarios.base} loadedHourlyRateUsd={loadedHourlyRateUsd} capacityRealization={capacityRealization} typingWordsPerMinute={typingWordsPerMinute} charactersPerWord={charactersPerWord} />
    <CalibrationSourceRegister sources={configuredSources} />
    <div className="calibration-actions">
      <button type="button" className="command-button" onClick={() => { setDraft(createDraft()); setActionStatus('Draft reset to the preserved presets.'); }}><RotateCcw size={15} />Reset to presets</button>
      <button type="button" className="command-button primary-command" disabled={Boolean(error)} onClick={copyConfiguration}><Copy size={15} />Copy config fragment</button>
      {actionStatus && <span className="muted" role="status">{actionStatus}</span>}
    </div>
  </div>;
}

function BaseInputEvidenceTable({ sources, base, loadedHourlyRateUsd, capacityRealization, typingWordsPerMinute, charactersPerWord }: {
  sources: CalibrationSource[];
  base: BenchmarkScenario;
  loadedHourlyRateUsd: number;
  capacityRealization: number;
  typingWordsPerMinute: number;
  charactersPerWord: number;
}) {
  const rows = [
    { label: 'Relevant token fraction', value: `${(base.planning.relevantTokenFraction * 100).toFixed(0)}% / ${(base.research.relevantTokenFraction * 100).toFixed(0)}% / ${(base.validation.relevantTokenFraction * 100).toFixed(0)}%`, target: 'relevant token fraction', need: 'Paired phase review samples' },
    { label: 'Reasoning token weight (P/V)', value: `${((base.planning.reasoningTokenWeight ?? 0) * 100).toFixed(0)}% / ${((base.validation.reasoningTokenWeight ?? 0) * 100).toFixed(0)}%`, target: 'reasoning token weight', need: 'Reasoning output is not shown to the developer' },
    { label: 'Human review rate', value: `${base.planning.tokensPerMinute} / ${base.research.tokensPerMinute} / ${base.validation.tokensPerMinute} tok/min`, target: 'human review rate', need: 'Timed local technical review' },
    { label: 'Tool interaction overhead', value: `${base.planning.interactionMinutesPerTool} / ${base.research.interactionMinutesPerTool} / ${base.validation.interactionMinutesPerTool} min/tool`, target: 'tool interaction overhead', need: 'Observed manual interaction timing' },
    { label: 'Manual source-entry fraction', value: `${(base.coding.manualEntryFraction * 100).toFixed(0)}%`, target: 'manual source-entry fraction', need: 'Paired retained-source attribution' },
    { label: 'Manual source-entry rate', value: `${base.coding.wordsPerMinute} words/min`, target: 'manual source-entry rate', need: 'Timed local coding tasks' },
    { label: 'Unclassified multiplier', value: `${base.unclassifiedManualMultiplier}x`, target: 'unclassified multiplier', need: 'Classified local task samples' },
    { label: 'Loaded labor rate', value: `$${loadedHourlyRateUsd.toFixed(0)}/hour`, target: 'loaded labor rate', need: 'Local finance record' },
    { label: 'Capacity realization', value: `${(capacityRealization * 100).toFixed(0)}%`, target: 'capacity-realization research question', need: 'Local operating policy or follow-through data' },
    { label: 'Audit typing rate', value: `${typingWordsPerMinute} words/min`, target: 'audit typing rate', need: 'Timed local transcription sample' },
    { label: 'Characters per word', value: String(charactersPerWord), target: 'characters per word', need: 'Documented conversion convention' },
    { label: 'Overall scenario envelope', value: 'External plausibility check', target: 'scenario envelope', need: 'Comparable controlled tasks' }
  ];
  return <div className="base-evidence-register">
    <div className="source-register-heading"><div><strong>Base input evidence coverage</strong><span>Only evidence mapped to an exact base input can justify that value. Context studies remain clearly labeled.</span></div></div>
    <div className="methodology-table-wrap"><table className="methodology-table base-evidence-table"><thead><tr><th>Base input</th><th>Preset value</th><th>Evidence status</th><th>Source or evidence needed</th></tr></thead><tbody>{rows.map((row) => {
      const matchingSources = sources.map((source, index) => ({ source, index })).filter(({ source }) => source.appliesTo.includes(row.target));
      const levels = matchingSources.map(({ source }) => source.supportLevels?.[row.target] ??
        (source.evidenceClass === 'local_measurement' ? 'direct' : 'context'));
      const support: CalibrationSupportLevel | null = levels.includes('direct')
        ? 'direct'
        : levels.includes('proxy')
          ? 'proxy'
          : levels.includes('context')
            ? 'context'
            : null;
      const status = support === 'direct'
        ? 'Direct support'
        : support === 'proxy'
          ? 'Proxy benchmark'
          : support === 'context'
            ? 'Context only'
            : 'Local evidence needed';
      return <tr key={row.label}><td>{row.label}</td><td>{row.value}</td><td><span className={`evidence-coverage coverage-${support ?? 'gap'}`}>{status}</span></td><td>{matchingSources.length ? matchingSources.map(({ source, index }) => <a key={source.url} href={`#calibration-source-${index + 1}`}>[{index + 1}] {source.publisher}</a>) : row.need}</td></tr>;
    })}</tbody></table></div>
  </div>;
}

function CalibrationSourceRegister({ sources }: { sources: CalibrationSource[] }) {
  return <div className="source-register">
    <div className="source-register-heading"><div><strong>Curated source register</strong><span>These citations are maintained with the model. They are not editable calibration inputs.</span></div><span>{sources.length} cited</span></div>
    {sources.length > 0 ? <div className="source-grid">{sources.map((source, index) => <article id={`calibration-source-${index + 1}`} className="source-card" key={`${source.url}-${index}`}>
      <div className="source-card-header"><span className={`source-class source-class-${source.evidenceClass}`}>{evidenceClassLabel(source.evidenceClass)}</span><span className="source-number">[{index + 1}]</span></div>
      <h3>{source.title}</h3>
      <p className="source-byline">{source.publisher} · {source.publishedAt}</p>
      <p><strong>Use:</strong> {source.finding}</p>
      <p className="source-limit"><strong>Limit:</strong> {source.limitation}</p>
      <div className="source-card-footer"><span>{source.appliesTo.join(' · ')}</span><a href={source.url} target="_blank" rel="noreferrer">Open source <ExternalLink size={13} /></a></div>
    </article>)}</div> : <p className="muted">No curated sources are configured.</p>}
  </div>;
}
