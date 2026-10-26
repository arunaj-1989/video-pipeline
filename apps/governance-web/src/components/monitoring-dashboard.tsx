"use client";

import Link from "next/link";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BadgeCheck,
  CheckCircle2,
  Clapperboard,
  ChevronDown,
  CircleAlert,
  CircleHelp,
  Clock3,
  Cloud,
  Cpu,
  Database,
  Gauge,
  HardDrive,
  LayoutDashboard,
  Orbit,
  Pause,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Video,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  agents,
  failureEvents,
  gpuJobs,
  monitoringRanges,
  overviewByRange,
  storageVolumes,
  type JobStatus,
  type MetricRange,
} from "@/lib/monitoring-data";

const agentIcons: Record<string, LucideIcon> = {
  orbit: Orbit,
  search: Search,
  shield: ShieldCheck,
  plan: LayoutDashboard,
};

const jobFilters: ("All jobs" | JobStatus)[] = [
  "All jobs",
  "Running",
  "Queued",
  "Failed",
  "Completed",
];

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone,
  trend,
}: {
  label: string;
  value: string;
  detail: string;
  icon: LucideIcon;
  tone: string;
  trend?: string;
}) {
  return (
    <article className="monitor-metric-card">
      <div className={`monitor-metric-icon ${tone}`}>
        <Icon size={18} strokeWidth={1.8} />
      </div>
      <div className="monitor-metric-label">{label}</div>
      <strong className="monitor-metric-value">{value}</strong>
      <span className="monitor-metric-detail">{detail}</span>
      {trend && <span className="monitor-metric-trend">{trend}</span>}
    </article>
  );
}

function PreviewNotice() {
  return (
    <div className="monitor-preview-notice" role="note">
      <span className="monitor-notice-icon">
        <Sparkles size={15} />
      </span>
      <p>
        <strong>Monitoring preview</strong>
        <span>
          Sample telemetry only. No live agents, RunPod jobs, budgets, or
          infrastructure are connected.
        </span>
      </p>
      <span className="monitor-demo-badge">DEMO DATA</span>
    </div>
  );
}

function HealthPill({
  children,
  tone = "healthy",
}: {
  children: React.ReactNode;
  tone?: "healthy" | "warning" | "error" | "neutral";
}) {
  return (
    <span className={`health-pill health-${tone}`}>
      <span />
      {children}
    </span>
  );
}

function RuntimeChart({
  values,
  labels,
}: {
  values: number[];
  labels: string[];
}) {
  const width = 540;
  const height = 168;
  const top = 10;
  const bottom = 137;
  const pointGap = width / (values.length - 1);
  const points = values
    .map((value, index) => {
      const y = bottom - (value / 100) * (bottom - top);
      return `${index * pointGap},${y}`;
    })
    .join(" ");
  const areaPoints = `0,${bottom} ${points} ${width},${bottom}`;

  return (
    <div className="runtime-chart">
      <div className="chart-legend">
        <span>
          <i className="legend-line" />
          Workflow runs
        </span>
        <strong>
          <span className="chart-live-dot" />
          SAMPLE SERIES
        </strong>
      </div>
      <div className="chart-frame">
        <div className="chart-y-axis" aria-hidden="true">
          <span>100</span>
          <span>75</span>
          <span>50</span>
          <span>25</span>
          <span>0</span>
        </div>
        <svg
          aria-label="Illustrative workflow activity trend"
          className="chart-svg"
          preserveAspectRatio="none"
          role="img"
          viewBox={`0 0 ${width} ${height}`}
        >
          <defs>
            <linearGradient id="monitor-chart-fill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#829bd0" stopOpacity="0.2" />
              <stop offset="100%" stopColor="#829bd0" stopOpacity="0.01" />
            </linearGradient>
          </defs>
          {[10, 42, 74, 106, 137].map((y) => (
            <line
              key={y}
              x1="0"
              x2={width}
              y1={y}
              y2={y}
              className="chart-gridline"
            />
          ))}
          <polygon points={areaPoints} className="chart-area" />
          <polyline points={points} className="chart-line" />
          {values.map((value, index) => {
            const y = bottom - (value / 100) * (bottom - top);
            return (
              <circle
                key={`${index}-${value}`}
                cx={index * pointGap}
                cy={y}
                r={index === values.length - 2 ? 4 : 2.2}
                className={index === values.length - 2 ? "chart-point-active" : "chart-point"}
              />
            );
          })}
        </svg>
      </div>
      <div className="chart-x-axis">
        {labels.map((time) => (
          <span key={time}>{time}</span>
        ))}
      </div>
    </div>
  );
}

function StatusSummary() {
  return (
    <section className="monitor-panel health-summary-panel">
      <div className="monitor-panel-heading">
        <div>
          <span className="monitor-eyebrow">SYSTEM CHECK</span>
          <h2>Service health</h2>
        </div>
        <span className="sample-signal">Illustrative status</span>
      </div>
      <div className="service-list">
        {[
          { name: "Control plane API", detail: "Local · Windows", icon: Server, tone: "healthy" as const, state: "Operational" },
          { name: "Workflow scheduler", detail: "Next run · 06:00 IST", icon: Clock3, tone: "healthy" as const, state: "Operational" },
          { name: "PostgreSQL", detail: "System of record", icon: Database, tone: "healthy" as const, state: "Operational" },
          { name: "ChromaDB index", detail: "Local vector store", icon: Database, tone: "healthy" as const, state: "Operational" },
          { name: "RunPod rendering", detail: "GPU worker plane", icon: Cloud, tone: "warning" as const, state: "2 active jobs" },
        ].map((service) => {
          const Icon = service.icon;
          return (
            <div className="service-row" key={service.name}>
              <span className={`service-icon service-${service.tone}`}>
                <Icon size={16} />
              </span>
              <span className="service-copy">
                <strong>{service.name}</strong>
                <small>{service.detail}</small>
              </span>
              <HealthPill tone={service.tone}>{service.state}</HealthPill>
            </div>
          );
        })}
      </div>
      <p className="panel-footnote">
        <ShieldAlert size={14} />
        Sample health states do not indicate live service readiness.
      </p>
    </section>
  );
}

function AgentStatusPanel() {
  return (
    <section className="monitor-panel agent-panel">
      <div className="monitor-panel-heading">
        <div>
          <span className="monitor-eyebrow">AUTONOMOUS WORKFLOWS</span>
          <h2>Agent activity</h2>
        </div>
        <span className="quiet-count">{agents.length} agents</span>
      </div>
      <div className="agent-table">
        <div className="agent-table-head">
          <span>AGENT</span>
          <span>STATE</span>
          <span>LAST RUN</span>
          <span>SUCCESS</span>
        </div>
        {agents.map((agent) => {
          const Icon = agentIcons[agent.icon];
          const tone =
            agent.state === "Running"
              ? "healthy"
              : agent.state === "Needs attention"
                ? "warning"
                : "neutral";
          return (
            <div className="agent-row" key={agent.name}>
              <span className="agent-name-cell">
                <span className={`agent-avatar agent-${tone}`}>
                  <Icon size={15} />
                </span>
                <span>
                  <strong>{agent.name}</strong>
                  <small>{agent.responsibility}</small>
                </span>
              </span>
              <HealthPill tone={tone}>{agent.state}</HealthPill>
              <span className="agent-last-run">
                {agent.lastRun}
                <small>{agent.duration} duration</small>
              </span>
              <strong className="agent-success">{agent.successRate}</strong>
            </div>
          );
        })}
      </div>
      <div className="panel-bottom-link">
        <span>Agent executions · sample 24-hour view</span>
        <span>
          18 runs <ArrowUpRight size={13} />
        </span>
      </div>
    </section>
  );
}

function CostBreakdown() {
  return (
    <section className="monitor-panel cost-panel">
      <div className="monitor-panel-heading">
        <div>
          <span className="monitor-eyebrow">GPU SPEND</span>
          <h2>Cost overview</h2>
        </div>
        <Wallet size={18} className="panel-heading-icon" />
      </div>
      <div className="cost-total">
        <div>
          <span>Sample spend this month</span>
          <strong>₹18,760</strong>
        </div>
        <span className="cost-target">of ₹75,000 budget</span>
      </div>
      <div
        aria-label="Illustrative monthly budget used: 25 percent"
        className="budget-track"
        role="img"
      >
        <span />
      </div>
      <div className="budget-caption">
        <span>25% used</span>
        <span>₹56,240 remaining</span>
      </div>
      <div className="cost-stats">
        <div>
          <span>Cost per video</span>
          <strong>₹2,345</strong>
        </div>
        <div>
          <span>Failed job cost</span>
          <strong>₹486</strong>
        </div>
        <div>
          <span>Projected month</span>
          <strong>₹64,200</strong>
        </div>
      </div>
      <p className="panel-footnote">
        <CircleHelp size={14} />
        Estimates are illustrative; no billing provider is connected.
      </p>
    </section>
  );
}

function GpuJobPanel({
  filter,
  onFilterChange,
}: {
  filter: (typeof jobFilters)[number];
  onFilterChange: (value: (typeof jobFilters)[number]) => void;
}) {
  const [query, setQuery] = useState("");
  const visibleJobs = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return gpuJobs.filter((job) => {
      const matchesFilter = filter === "All jobs" || job.status === filter;
      const matchesQuery =
        !normalized ||
        [job.id, job.episode, job.task, job.gpu].some((value) =>
          value.toLowerCase().includes(normalized),
        );
      return matchesFilter && matchesQuery;
    });
  }, [filter, query]);

  return (
    <section className="monitor-panel jobs-panel">
      <div className="monitor-panel-heading">
        <div>
          <span className="monitor-eyebrow">RENDERING PLANE</span>
          <h2>GPU jobs</h2>
        </div>
        <div className="job-heading-actions">
          <HealthPill tone="warning">2 active</HealthPill>
          <button
            aria-label="Clear GPU job search"
            className="monitor-icon-button"
            onClick={() => setQuery("")}
            title="Clear search"
            type="button"
          >
            <RefreshCw size={15} />
          </button>
        </div>
      </div>
      <div className="job-toolbar">
        <div aria-label="Filter GPU jobs" className="job-filters" role="group">
          {jobFilters.map((jobFilter) => (
            <button
              aria-pressed={filter === jobFilter}
              className={`job-filter${filter === jobFilter ? " is-selected" : ""}`}
              key={jobFilter}
              onClick={() => onFilterChange(jobFilter)}
              type="button"
            >
              {jobFilter}
            </button>
          ))}
        </div>
        <label className="job-search">
          <Search size={14} />
          <input
            aria-label="Search GPU jobs"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search jobs"
            value={query}
          />
        </label>
      </div>
      <div className="gpu-job-list">
        {visibleJobs.length === 0 ? (
          <div className="monitor-empty-state">
            <Video size={20} />
            <strong>No matching jobs</strong>
            <span>Change the status filter or search term.</span>
          </div>
        ) : (
          visibleJobs.map((job) => (
            <article className="gpu-job-row" key={job.id}>
              <span className={`job-gpu-icon job-status-${job.status.toLowerCase()}`}>
                <Cpu size={17} />
              </span>
              <div className="job-main">
                <div className="job-title-line">
                  <strong>{job.episode}</strong>
                  <span>{job.id}</span>
                </div>
                <span className="job-task">{job.task}</span>
                <div className="job-progress-line">
                  <span className="job-progress-track">
                    <span
                      className={`job-progress-fill job-fill-${job.status.toLowerCase()}`}
                      style={{ width: `${job.progress}%` }}
                    />
                  </span>
                  <span>{job.progress}%</span>
                </div>
              </div>
              <span className="job-gpu">{job.gpu}</span>
              <HealthPill
                tone={
                  job.status === "Running"
                    ? "healthy"
                    : job.status === "Failed"
                      ? "error"
                      : "neutral"
                }
              >
                {job.status}
              </HealthPill>
              <span className="job-runtime">{job.elapsed}</span>
              <strong className="job-cost">{job.cost}</strong>
            </article>
          ))
        )}
      </div>
      <div className="panel-bottom-link">
        <span>Sample job telemetry · updated 18:30 IST</span>
        <span>
          Estimated GPU cost <ArrowUpRight size={13} />
        </span>
      </div>
    </section>
  );
}

function FailurePanel() {
  return (
    <section className="monitor-panel failure-panel">
      <div className="monitor-panel-heading">
        <div>
          <span className="monitor-eyebrow">ATTENTION REQUIRED</span>
          <h2>Recent failures &amp; retries</h2>
        </div>
        <span className="failure-count">3 events</span>
      </div>
      <div className="failure-list">
        {failureEvents.map((failure) => (
          <div className="failure-row" key={failure.id}>
            <span className={`failure-icon failure-${failure.severity}`}>
              {failure.severity === "error" ? (
                <CircleAlert size={16} />
              ) : (
                <TriangleAlert size={16} />
              )}
            </span>
            <span className="failure-copy">
              <strong>{failure.title}</strong>
              <small>{failure.detail}</small>
            </span>
            <span className="failure-time">{failure.age}</span>
          </div>
        ))}
      </div>
      <p className="panel-footnote">
        <RefreshCw size={13} />
        Retry state is sample data; no job action is available here.
      </p>
    </section>
  );
}

function StoragePanel() {
  return (
    <section className="monitor-panel storage-panel">
      <div className="monitor-panel-heading">
        <div>
          <span className="monitor-eyebrow">LOCAL RESOURCES</span>
          <h2>Storage &amp; capacity</h2>
        </div>
        <HardDrive size={18} className="panel-heading-icon" />
      </div>
      <div className="disk-summary">
        <span className="disk-icon">
          <HardDrive size={17} />
        </span>
        <span>
          <strong>Local workspace disk</strong>
          <small>SSD · sample utilization</small>
        </span>
        <strong className="disk-total">331 / 512 GB</strong>
      </div>
      <div className="disk-track">
        <span />
      </div>
      <div className="storage-volumes">
        {storageVolumes.map((volume) => (
          <div className="storage-row" key={volume.name}>
            <span className="storage-name">
              <span className={volume.color} />
              {volume.name}
            </span>
            <span className="storage-track">
              <span className={volume.color} style={{ width: `${volume.percentage}%` }} />
            </span>
            <strong>{volume.used}</strong>
          </div>
        ))}
      </div>
      <p className="panel-footnote">
        <HardDrive size={13} />
        Disk values are examples, not measured laptop usage.
      </p>
    </section>
  );
}

function RenderPipelinePanel() {
  const stages = [
    { name: "Queued", count: "03", width: "28%", tone: "pipeline-queued" },
    { name: "Generating", count: "02", width: "54%", tone: "pipeline-generating" },
    { name: "Assembling", count: "01", width: "72%", tone: "pipeline-assembling" },
    { name: "Ready for review", count: "02", width: "100%", tone: "pipeline-ready" },
  ];

  return (
    <section className="monitor-panel render-pipeline-panel">
      <div className="monitor-panel-heading">
        <div>
          <span className="monitor-eyebrow">CONTENT PRODUCTION</span>
          <h2>Rendering progress</h2>
        </div>
        <Video size={18} className="panel-heading-icon" />
      </div>
      <div className="pipeline-stage-list">
        {stages.map((stage) => (
          <div className="pipeline-stage" key={stage.name}>
            <span className="pipeline-stage-name">{stage.name}</span>
            <span className="pipeline-stage-track">
              <span className={stage.tone} style={{ width: stage.width }} />
            </span>
            <strong>{stage.count}</strong>
          </div>
        ))}
      </div>
      <div className="pipeline-summary">
        <span>
          <CheckCircle2 size={14} />
          2 episodes are ready for editorial review
        </span>
        <Link href="/" className="pipeline-link">
          Open governance <ArrowRight size={13} />
        </Link>
      </div>
    </section>
  );
}

export function MonitoringDashboard() {
  const [range, setRange] = useState<MetricRange>("24 hours");
  const [jobFilter, setJobFilter] = useState<(typeof jobFilters)[number]>("All jobs");
  const [showRangeMenu, setShowRangeMenu] = useState(false);
  const metrics = overviewByRange[range];
  const chartLabels =
    range === "24 hours"
      ? ["06:00", "09:00", "12:00", "15:00", "18:00"]
      : range === "7 days"
        ? ["Mon", "Tue", "Wed", "Thu", "Fri"]
        : ["Oct 01", "Oct 08", "Oct 15", "Oct 22", "Oct 26"];

  return (
    <div className="monitor-shell">
      <aside className="monitor-sidebar">
        <Link className="brand-lockup" href="/">
          <span className="brand-mark" aria-hidden="true">
            <Clapperboard size={22} strokeWidth={1.8} />
            <span />
          </span>
          <span className="brand-copy">
            <strong>universal video</strong>
            <span>MAKER STUDIO</span>
          </span>
        </Link>

        <div className="monitor-workspace-switcher">
          <span className="workspace-avatar">UV</span>
          <span className="workspace-copy">
            <strong>Video control plane</strong>
            <span>Local · Windows</span>
          </span>
          <span className="workspace-status-dot" />
        </div>

        <div className="monitor-side-label">OPERATIONS</div>
        <nav className="monitor-side-nav" aria-label="Operations navigation">
          <a className="monitor-nav-link is-active" href="#overview" aria-current="page">
            <Gauge size={17} />
            <span>System overview</span>
          </a>
          <a className="monitor-nav-link" href="#agents">
            <Activity size={17} />
            <span>Agent activity</span>
            <span className="monitor-nav-count">4</span>
          </a>
          <a className="monitor-nav-link" href="#gpu-jobs">
            <Cpu size={17} />
            <span>GPU jobs</span>
            <span className="monitor-nav-count">4</span>
          </a>
          <a className="monitor-nav-link" href="#failures">
            <TriangleAlert size={17} />
            <span>Failures &amp; retries</span>
            <span className="monitor-nav-count monitor-nav-alert">3</span>
          </a>
          <a className="monitor-nav-link" href="#costs">
            <Wallet size={17} />
            <span>Cost overview</span>
          </a>
          <a className="monitor-nav-link" href="#storage">
            <HardDrive size={17} />
            <span>Storage</span>
          </a>
        </nav>

        <div className="monitor-plane-card">
          <span className="plane-card-icon">
            <Cloud size={16} />
          </span>
          <span className="plane-card-copy">
            <strong>RunPod plane</strong>
            <small>Remote GPU capacity</small>
          </span>
          <HealthPill tone="warning">Demo</HealthPill>
          <p>GPU work is remote. Workflow ownership stays on the local control plane.</p>
        </div>
        <div className="monitor-side-bottom">
          <Link className="monitor-back-link" href="/">
            <LayoutDashboard size={16} />
            Governance Studio
            <ArrowUpRight size={13} />
          </Link>
          <div className="monitor-profile">
            <span className="profile-avatar">OP</span>
            <span className="profile-copy">
              <strong>Operations view</strong>
              <span>Preview workspace</span>
            </span>
          </div>
        </div>
      </aside>

      <div className="monitor-main">
        <header className="monitor-topbar">
          <div className="monitor-breadcrumb">
            <span>Universal Video Maker</span>
            <span>/</span>
            <strong>Monitoring</strong>
          </div>
          <div className="monitor-top-actions">
            <HealthPill tone="warning">Preview data</HealthPill>
            <span className="monitor-updated">
              <Clock3 size={13} />
              Sample snapshot · 18:30 IST
            </span>
            <span className="topbar-avatar">OP</span>
          </div>
        </header>

        <main className="monitor-content" id="overview">
          <div className="monitor-mobile-nav">
            <Link className="monitor-mobile-link" href="/">
              <LayoutDashboard size={14} />
              Governance
            </Link>
            <span className="monitor-mobile-link is-current">
              <Activity size={14} />
              Monitoring
            </span>
          </div>

          <div className="monitor-page-heading">
            <div>
              <div className="monitor-heading-eyebrow">
                <span>PLATFORM HEALTH</span>
                <i />
                <span>MONDAY, 26 OCTOBER 2026</span>
              </div>
              <h1>Operations at a glance</h1>
              <p>
                Workflow health, rendering activity, and cost signals in one
                place.
              </p>
            </div>
            <div className="monitor-heading-actions">
              <div className="range-picker-wrap">
                <button
                  aria-expanded={showRangeMenu}
                  className="range-picker"
                  onClick={() => setShowRangeMenu((open) => !open)}
                  type="button"
                >
                  <Clock3 size={14} />
                  {range}
                  <ChevronDown size={14} />
                </button>
                {showRangeMenu && (
                  <div className="range-menu" role="group" aria-label="Metric time range">
                    {monitoringRanges.map((item) => (
                      <button
                        aria-pressed={range === item}
                        className={range === item ? "range-option is-selected" : "range-option"}
                        key={item}
                        onClick={() => {
                          setRange(item);
                          setShowRangeMenu(false);
                        }}
                        type="button"
                      >
                        {item}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button
                className="monitor-refresh-button"
                type="button"
                disabled
                title="Live telemetry is not connected"
              >
                <RefreshCw size={15} />
                Live refresh unavailable
              </button>
            </div>
          </div>

          <PreviewNotice />

          <section aria-label="Key monitoring metrics" className="monitor-metric-grid">
            <MetricCard
              detail="2 workflows currently running"
              icon={Activity}
              label="Workflow runs"
              tone="metric-blue"
              trend="+3 from previous period"
              value={metrics.workflowRuns}
            />
            <MetricCard
              detail="Across scheduled and manual runs"
              icon={BadgeCheck}
              label="Workflow success"
              tone="metric-green"
              trend="+1.4% from previous period"
              value={metrics.successRate}
            />
            <MetricCard
              detail="2 running · 1 queued"
              icon={Cpu}
              label="GPU jobs"
              tone="metric-orange"
              trend="1 failed job"
              value={metrics.gpuJobs}
            />
            <MetricCard
              detail="₹75,000 illustrative monthly budget"
              icon={Wallet}
              label="RunPod spend"
              tone="metric-violet"
              trend="25% of sample budget"
              value={metrics.spend}
            />
          </section>

          <div className="monitor-primary-grid">
            <section className="monitor-panel activity-chart-panel">
              <div className="monitor-panel-heading">
                <div>
                  <span className="monitor-eyebrow">WORKFLOW THROUGHPUT</span>
                  <h2>Activity over time</h2>
                </div>
                <HealthPill tone="healthy">Stable</HealthPill>
              </div>
              <div className="chart-summary">
                <span>
                  <strong>{metrics.workflowRuns}</strong>
                  <small>sample runs</small>
                </span>
                <span className="chart-change">
                  <ArrowUpRight size={14} />
                  12% vs previous period
                </span>
              </div>
              <RuntimeChart values={metrics.chartValues} labels={chartLabels} />
            </section>

            <section className="monitor-panel system-panel">
              <div className="monitor-panel-heading">
                <div>
                  <span className="monitor-eyebrow">CONTROL + RENDER</span>
                  <h2>Pipeline health</h2>
                </div>
                <Activity size={18} className="panel-heading-icon" />
              </div>
              <div className="pipeline-health-score">
                <span className="health-score-ring">
                  <strong>94</strong>
                  <small>/100</small>
                </span>
                <span>
                  <strong>Healthy with attention</strong>
                  <small>One retry and one failed render in this sample.</small>
                </span>
              </div>
              <div className="mini-health-list">
                <div>
                  <span><Server size={14} /> Local control plane</span>
                  <HealthPill tone="healthy">Operational</HealthPill>
                </div>
                <div>
                  <span><Cloud size={14} /> RunPod render plane</span>
                  <HealthPill tone="warning">Jobs active</HealthPill>
                </div>
                <div>
                  <span><TriangleAlert size={14} /> Open incidents</span>
                  <HealthPill tone="warning">3 events</HealthPill>
                </div>
              </div>
              <div className="control-plane-note">
                <Pause size={13} />
                Local laptop must remain awake for scheduled runs.
              </div>
            </section>
          </div>

          <div className="monitor-secondary-grid">
            <div className="monitor-column">
              <div id="agents">
                <AgentStatusPanel />
              </div>
              <div id="gpu-jobs">
                <GpuJobPanel filter={jobFilter} onFilterChange={setJobFilter} />
              </div>
            </div>
            <div className="monitor-column">
              <div id="costs">
                <CostBreakdown />
              </div>
              <StatusSummary />
              <div id="failures">
                <FailurePanel />
              </div>
              <div id="storage">
                <StoragePanel />
              </div>
              <RenderPipelinePanel />
            </div>
          </div>

          <footer className="monitor-footer">
            <span>Observability preview · operational data is not connected</span>
            <span>
              <ShieldCheck size={13} />
              RunPod is the rendering plane · laptop is the control plane
            </span>
          </footer>
        </main>
      </div>
    </div>
  );
}
