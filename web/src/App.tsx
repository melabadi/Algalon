import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Activity,
  BarChart3,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  MessageSquareText,
  RefreshCw,
  Search
} from 'lucide-react';
import {
  activeCalibrationStorageKey,
  loadSavedCalibrations,
  savedCalibrationStorageKey,
  scenarioSelections,
  type SavedCalibration,
  type ScenarioSelection
} from './features/calibration/model';
import { MethodologyView } from './features/methodology/MethodologyView';
import { InsightsView } from './features/insights/InsightsView';
import { OverallView, PromptView, PromptsView, SessionView } from './features/sessions/SessionViews';
import { AutoRefreshContext } from './hooks/useLoad';
import { messages } from './i18n';
import { scenarioLabel } from './lib/format';
import { pagePath, parsePage } from './lib/routes';
import type { Page } from './types';

type RefreshSeconds = 0 | 5 | 15 | 30 | 60;

const refreshOptions = messages.controls.refreshOptions;
const metricHelp = messages.metricHelp;

function HierarchyNavigation({ page, navigate }: { page: Page; navigate: (page: Page) => void }) {
  const activeLevel = useRef<HTMLButtonElement>(null);
  const experiment = page.name === 'session' || page.name === 'prompts' || page.name === 'prompt' ? page.experiment : null;
  const depth = page.name === 'overall' ? 0 : page.name === 'session' ? 1 : page.name === 'prompts' ? 2 : page.name === 'prompt' ? 3 : -1;
  const levels: Array<{ label: string; icon: ReactNode; target: Page | null }> = [
    { label: messages.shell.levels.overall, icon: <BarChart3 size={15} />, target: { name: 'overall' } },
    { label: messages.shell.levels.session, icon: <Activity size={15} />, target: experiment ? { name: 'session', experiment } : null },
    { label: messages.shell.levels.prompts, icon: <MessageSquareText size={15} />, target: experiment ? { name: 'prompts', experiment } : null },
    { label: messages.shell.levels.promptDetail, icon: <Search size={15} />, target: page.name === 'prompt' ? page : null }
  ];

  useEffect(() => {
    activeLevel.current?.scrollIntoView({ block: 'nearest', inline: 'center' });
  }, [page.name]);

  return (
    <nav className="navigation" aria-label={messages.shell.drillDownHierarchy}>
      <div className="hierarchy-levels">
        {levels.map((level, index) => {
          const current = depth === index;
          const complete = depth > index;
          const available = level.target !== null && index <= depth + 1;
          return (
            <div className="hierarchy-level" key={level.label}>
              <button ref={current ? activeLevel : undefined} className={`hierarchy-button${current ? ' active' : ''}${complete ? ' complete' : ''}`} disabled={!available} aria-current={current ? 'page' : undefined} onClick={() => level.target && !current && navigate(level.target)}>
                <span className="level-number">{index + 1}</span>
                <span className="level-copy"><span className="level-kicker">{messages.shell.level(index + 1)}</span><span className="level-label">{level.icon}{level.label}</span></span>
              </button>
              {index < levels.length - 1 && <ChevronRight className={`hierarchy-connector${index < depth ? ' complete' : ''}`} size={15} aria-hidden="true" />}
            </div>
          );
        })}
      </div>
      <div className="reference-links">
        <button ref={page.name === 'insights' ? activeLevel : undefined} className={`reference-link${page.name === 'insights' ? ' active' : ''}`} onClick={() => navigate({ name: 'insights' })}><BarChart3 size={16} />{messages.shell.insights}</button>
        <button ref={page.name === 'methodology' ? activeLevel : undefined} className={`reference-link${page.name === 'methodology' ? ' active' : ''}`} onClick={() => navigate({ name: 'methodology' })}><BookOpen size={16} />{messages.shell.methodology}</button>
      </div>
    </nav>
  );
}

export function App() {
  const [page, setPage] = useState<Page>(() => parsePage());
  const [scenario, setScenario] = useState<ScenarioSelection>('base');
  const [days, setDays] = useState(30);
  const [savedCalibrations, setSavedCalibrations] = useState<SavedCalibration[]>(loadSavedCalibrations);
  const [activeCalibrationId, setActiveCalibrationId] = useState<string | null>(() => window.localStorage.getItem(activeCalibrationStorageKey));
  const [refreshSeconds, setRefreshSeconds] = useState<RefreshSeconds>(() => {
    const saved = window.localStorage.getItem('algalon.autoRefreshSeconds');
    if (saved === null) return 15;
    const stored = Number(saved);
    return refreshOptions.some((option) => option.seconds === stored) ? stored as RefreshSeconds : 15;
  });

  useEffect(() => {
    const popstate = () => setPage(parsePage());
    window.addEventListener('popstate', popstate);
    return () => window.removeEventListener('popstate', popstate);
  }, []);

  useEffect(() => {
    window.localStorage.setItem('algalon.autoRefreshSeconds', String(refreshSeconds));
  }, [refreshSeconds]);

  useEffect(() => {
    window.localStorage.setItem(savedCalibrationStorageKey, JSON.stringify(savedCalibrations));
  }, [savedCalibrations]);

  const activeCalibration = savedCalibrations.find((calibration) => calibration.id === activeCalibrationId);
  const selectedScenario: ScenarioSelection = scenario === 'custom' && !activeCalibration ? 'base' : scenario;

  useEffect(() => {
    if (activeCalibrationId && !activeCalibration) {
      setActiveCalibrationId(null);
      return;
    }
    if (!activeCalibration && scenario === 'custom') setScenario('base');
    if (activeCalibrationId) window.localStorage.setItem(activeCalibrationStorageKey, activeCalibrationId);
    else window.localStorage.removeItem(activeCalibrationStorageKey);
  }, [activeCalibration, activeCalibrationId, scenario]);

  const navigate = (nextPage: Page) => {
    window.history.pushState({}, '', pagePath(nextPage));
    setPage(nextPage);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <AutoRefreshContext.Provider value={refreshSeconds * 1_000}>
    <main className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => navigate({ name: 'overall' })}><span className="brand-mark" />{messages.shell.brand}</button>
        <span className="local-status"><CheckCircle2 size={13} />{messages.shell.localEvidenceCurrent}</span>
        <div className="scenario-selector"><span className="control-label">{activeCalibration ? messages.shell.customScenario(activeCalibration.name) : messages.shell.scenario}</span><div className="scenario-control" role="group" aria-label={messages.shell.roiScenario}>{scenarioSelections.map((name) => <button key={name} className={selectedScenario === name ? 'active' : ''} disabled={name === 'custom' && !activeCalibration} title={name === 'custom' && !activeCalibration ? messages.shell.chooseCalibration : undefined} onClick={() => setScenario(name)}>{scenarioLabel(name)}</button>)}</div></div>
        <select className="range-select" value={days} onChange={(event) => setDays(Number(event.target.value))} aria-label={messages.shell.timeRange}>{messages.shell.ranges.map((range) => <option key={range.days} value={range.days}>{range.label}</option>)}</select>
        <label className="refresh-control"><RefreshCw size={14} aria-hidden="true" /><select value={refreshSeconds} onChange={(event) => setRefreshSeconds(Number(event.target.value) as RefreshSeconds)} aria-label={messages.shell.autoRefreshInterval}>{refreshOptions.map((option) => <option key={option.seconds} value={option.seconds}>{option.label}</option>)}</select></label>
      </header>
      <HierarchyNavigation page={page} navigate={navigate} />
      <section className="page-content">
        {page.name === 'overall' && <OverallView scenario={selectedScenario} days={days} calibration={activeCalibration} navigate={navigate} />}
        {page.name === 'session' && <SessionView experiment={page.experiment} scenario={selectedScenario} calibration={activeCalibration} navigate={navigate} />}
        {page.name === 'prompts' && <PromptsView experiment={page.experiment} scenario={selectedScenario} calibration={activeCalibration} navigate={navigate} />}
        {page.name === 'prompt' && <PromptView promptId={page.promptId} experiment={page.experiment} scenario={selectedScenario} calibration={activeCalibration} navigate={navigate} />}
        {page.name === 'insights' && <InsightsView days={days} />}
        {page.name === 'methodology' && <MethodologyView scenario={selectedScenario} savedCalibrations={savedCalibrations} setSavedCalibrations={setSavedCalibrations} activeCalibrationId={activeCalibrationId} setActiveCalibrationId={setActiveCalibrationId} />}
      </section>
    </main>
    </AutoRefreshContext.Provider>
  );
}