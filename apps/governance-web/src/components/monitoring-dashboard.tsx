"use client";

import Link from "next/link";
import {
  Activity,
  ArrowUpRight,
  BadgeCheck,
  ChevronDown,
  CircleAlert,
  Clapperboard,
  Clock3,
  Cloud,
  Cpu,
  Database,
  Gauge,
  HardDrive,
  LayoutDashboard,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  TriangleAlert,
  Video,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import type Keycloak from "keycloak-js";
import { useCallback, useEffect, useMemo, useState } from "react";

import { KeycloakGate } from "@/components/keycloak-gate";
import { getMonitoringOverview, type MonitoringOverview } from "@/lib/operations-api";

const ranges = [
  { label: "24 hours", hours: 24 },
  { label: "7 days", hours: 168 },
  { label: "30 days", hours: 720 },
] as const;

const jobFilters = ["All jobs", "Active", "Failed", "Completed"] as const;
type JobFilter = (typeof jobFilters)[number];

const ACTIVE_STATUSES = new Set(["queued", "submitted", "running"]);

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  icon: LucideIcon;
  tone: string;
}) {
  return (
    <article className="monitor-metric-card">
      <div className={`monitor-metric-icon ${tone}`}>
        <Icon size={18} strokeWidth={1.8} />
      </div>
      <div className="monitor-metric-label">{label}</div>
      <strong className="monitor-metric-value">{value}</strong>
      <span className="monitor-metric-detail">{detail}</span>
    </article>
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

function NotConnected({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="monitor-empty-state">
      <CircleAlert size={20} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function formatDuration(seconds: number | null) {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  return minutes > 0 ? `${minutes}m ${seconds % 60}s` : `${seconds}s`;
}

function formatCost(value: string | null) {
  if (value === null) return "—";
  return `$${Number(value).toFixed(2)}`;
}

function formatAge(value: string) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

function jobTone(status: string): "healthy" | "error" | "neutral" | "warning" {
  if (status === "running" || status === "completed") return "healthy";
  if (status === "failed") return "error";
  if (ACTIVE_STATUSES.has(status)) return "warning";
  return "neutral";
}

function ActivityChart({ series, hours }: { series: MonitoringOverview["series"]; hours: number }) {
  const width = 540;
  const height = 168;
  const top = 10;
  const bottom = 137;
  const max = Math.max(1, ...series.map((bucket) => bucket.workflow_runs));
  const pointGap = width / Math.max(1, series.length - 1);
  const yFor = (value: number) => bottom - (value / max) * (bottom - top);
  const points = series
    .map((bucket, index) => `${index * pointGap},${yFor(bucket.workflow_runs)}`)
    .join(" ");
  const formatLabel = (value: string) =>
    new Date(value).toLocaleString(undefined, hours <= 24
      ? { hour: "2-digit", minute: "2-digit" }
      : { day: "numeric", month: "short" });
  const labelIndexes = [0, Math.floor(series.length / 4), Math.floor(series.length / 2), Math.floor((3 * series.length) / 4), series.length - 1];

  return (
    <div className="runtime-chart">
      <div className="chart-legend">
        <span>
          <i className="legend-line" />
          Workflow runs started
        </span>
        <strong>
          <span className="chart-live-dot" />
          RECORDED
        </strong>
      </div>
      <div className="chart-frame">
        <div className="chart-y-axis" aria-hidden="true">
          <span>{max}</span>
          <span>{Math.round(max * 0.75)}</span>
          <span>{Math.round(max * 0.5)}</span>
          <span>{Math.round(max * 0.25)}</span>
          <span>0</span>
        </div>
        <svg
          aria-label="Workflow runs over time"
          className="chart-svg"
          preserveAspectRatio="none"
          role="img"
          viewBox={`0 0 ${width} ${height}`}
        >
          {[10, 42, 74, 106, 137].map((y) => (
            <line key={y} x1="0" x2={width} y1={y} y2={y} className="chart-gridline" />
          ))}
          <polygon points={`0,${bottom} ${points} ${width},${bottom}`} className="chart-area" />
          <polyline points={points} className="chart-line" />
          {series.map((bucket, index) => (
            <circle
              key={bucket.start}
              cx={index * pointGap}
              cy={yFor(bucket.workflow_runs)}
              r={2.2}
              className="chart-point"
            />
          ))}
        </svg>
      </div>
      <div className="chart-x-axis">
        {labelIndexes.map((index) => (
          <span key={index}>{formatLabel(series[index].start)}</span>
        ))}
      </div>
    </div>
  );
}

function JobsPanel({ jobs }: { jobs: MonitoringOverview["recent_jobs"] }) {
  const [filter, setFilter] = useState<JobFilter>("All jobs");
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return jobs.filter((job) => {
      const matchesFilter =
        filter === "All jobs" ||
        (filter === "Active" && ACTIVE_STATUSES.has(job.status)) ||
        (filter === "Failed" && job.status === "failed") ||
        (filter === "Completed" && job.status === "completed");
      const matchesQuery =
        !normalized ||
        [job.id, job.episode_title, job.provider, job.remote_job_id ?? ""].some((value) =>
          value.toLowerCase().includes(normalized),
        );
      return matchesFilter && matchesQuery;
    });
  }, [filter, jobs, query]);

  return (
    <section className="monitor-panel jobs-panel">
      <div className="monitor-panel-heading">
        <div>
          <span className="monitor-eyebrow">RENDERING PLANE</span>
          <h2>Render jobs</h2>
        </div>
        <span className="quiet-count">{jobs.length} recent</span>
      </div>
      <div className="job-toolbar">
        <div aria-label="Filter render jobs" className="job-filters" role="group">
          {jobFilters.map((item) => (
            <button
              aria-pressed={filter === item}
              className={`job-filter${filter === item ? " is-selected" : ""}`}
              key={item}
              onClick={() => setFilter(item)}
              type="button"
            >
              {item}
            </button>
          ))}
        </div>
        <label className="job-search">
          <Search size={14} />
          <input
            aria-label="Search render jobs"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search jobs"
            value={query}
          />
        </label>
      </div>
      <div className="gpu-job-list">
        {visible.length === 0 ? (
          <div className="monitor-empty-state">
            <Video size={20} />
            <strong>{jobs.length === 0 ? "No render jobs recorded" : "No matching jobs"}</strong>
            <span>
              {jobs.length === 0
                ? "Jobs appear here once an episode is submitted for rendering."
                : "Change the status filter or search term."}
            </span>
          </div>
        ) : (
          visible.map((job) => (
            <article className="gpu-job-row" key={job.id}>
              <span className={`job-gpu-icon job-status-${job.status}`}>
                <Cpu size={17} />
              </span>
              <div className="job-main">
                <div className="job-title-line">
                  <strong>{job.episode_title}</strong>
                  <span>{job.remote_job_id ?? job.id.slice(0, 8)}</span>
                </div>
                <span className="job-task">
                  {job.failure_reason ?? `Submitted ${formatAge(job.created_at)}`}
                </span>
              </div>
              <span className="job-gpu">{job.provider}</span>
              <HealthPill tone={jobTone(job.status)}>{job.status}</HealthPill>
              <span className="job-runtime">{formatDuration(job.runtime_seconds)}</span>
              <strong className="job-cost">{formatCost(job.cost)}</strong>
            </article>
          ))
        )}
      </div>
    </section>
  );
}

function FailurePanel({ failures }: { failures: MonitoringOverview["failures"] }) {
  return (
    <section className="monitor-panel failure-panel">
      <div className="monitor-panel-heading">
        <div>
          <span className="monitor-eyebrow">ATTENTION REQUIRED</span>
          <h2>Recent failures</h2>
        </div>
        <span className="failure-count">{failures.length} events</span>
      </div>
      <div className="failure-list">
        {failures.length === 0 ? (
          <NotConnected
            title="No failures recorded"
            detail="Failed workflow steps and render jobs will be listed here."
          />
        ) : (
          failures.map((failure) => (
            <div className="failure-row" key={failure.id}>
              <span className="failure-icon failure-error">
                <TriangleAlert size={16} />
              </span>
              <span className="failure-copy">
                <strong>{failure.title}</strong>
                <small>{failure.detail}</small>
              </span>
              <span className="failure-time">{formatAge(failure.occurred_at)}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function MonitoringContent({ keycloak }: { keycloak: Keycloak }) {
  const [range, setRange] = useState<(typeof ranges)[number]>(ranges[0]);
  const [showRangeMenu, setShowRangeMenu] = useState(false);
  const [overview, setOverview] = useState<MonitoringOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  const refresh = useCallback(() => setReload((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError("");
      try {
        await keycloak.updateToken(30);
        if (!keycloak.token) throw new Error("Your session expired. Sign in again.");
        const data = await getMonitoringOverview(keycloak.token, range.hours);
        if (active) setOverview(data);
      } catch (loadError) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Could not load monitoring data.");
        }
      } finally {
        if (active) setLoading(false);
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, [keycloak, range, reload]);

  const finished = overview ? overview.workflow_completed + overview.workflow_failed : 0;
  const successRate =
    overview && finished > 0
      ? `${Math.round((overview.workflow_completed / finished) * 100)}%`
      : "—";
  const apiHealthy = overview !== null && error === "";

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

        <div className="monitor-side-label">OPERATIONS</div>
        <nav className="monitor-side-nav" aria-label="Operations navigation">
          <a className="monitor-nav-link is-active" href="#overview" aria-current="page">
            <Gauge size={17} />
            <span>System overview</span>
          </a>
          <a className="monitor-nav-link" href="#gpu-jobs">
            <Cpu size={17} />
            <span>Render jobs</span>
            {overview && <span className="monitor-nav-count">{overview.render_active}</span>}
          </a>
          <a className="monitor-nav-link" href="#failures">
            <TriangleAlert size={17} />
            <span>Failures</span>
            {overview && overview.failures.length > 0 && (
              <span className="monitor-nav-count monitor-nav-alert">{overview.failures.length}</span>
            )}
          </a>
          <a className="monitor-nav-link" href="#costs">
            <Wallet size={17} />
            <span>Cost overview</span>
          </a>
        </nav>

        <div className="monitor-side-bottom">
          <Link className="monitor-back-link" href="/">
            <LayoutDashboard size={16} />
            Governance Studio
            <ArrowUpRight size={13} />
          </Link>
          <button
            className="monitor-back-link"
            onClick={() => void keycloak.logout({ redirectUri: window.location.origin })}
            type="button"
          >
            Sign out
          </button>
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
            <HealthPill tone={error ? "error" : loading && !overview ? "neutral" : "healthy"}>
              {error ? "Unavailable" : loading && !overview ? "Loading" : "Live data"}
            </HealthPill>
            {overview && (
              <span className="monitor-updated">
                <Clock3 size={13} />
                Updated {new Date(overview.generated_at).toLocaleTimeString()}
              </span>
            )}
          </div>
        </header>

        <main className="monitor-content" id="overview">
          <div className="monitor-page-heading">
            <div>
              <div className="monitor-heading-eyebrow">
                <span>PLATFORM HEALTH</span>
              </div>
              <h1>Operations at a glance</h1>
              <p>Workflow health, rendering activity, and cost recorded in PostgreSQL.</p>
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
                  {range.label}
                  <ChevronDown size={14} />
                </button>
                {showRangeMenu && (
                  <div className="range-menu" role="group" aria-label="Metric time range">
                    {ranges.map((item) => (
                      <button
                        aria-pressed={range.hours === item.hours}
                        className={
                          range.hours === item.hours ? "range-option is-selected" : "range-option"
                        }
                        key={item.hours}
                        onClick={() => {
                          setRange(item);
                          setShowRangeMenu(false);
                        }}
                        type="button"
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button
                className="monitor-refresh-button"
                disabled={loading}
                onClick={refresh}
                type="button"
              >
                <RefreshCw size={15} />
                {loading ? "Refreshing..." : "Refresh"}
              </button>
            </div>
          </div>

          {error && (
            <p className="monitor-preview-notice" role="alert">
              Could not load monitoring data: {error}
            </p>
          )}

          <section aria-label="Key monitoring metrics" className="monitor-metric-grid">
            <MetricCard
              detail={`${overview?.workflow_running ?? 0} currently running`}
              icon={Activity}
              label="Workflow runs"
              tone="metric-blue"
              value={String(overview?.workflow_runs ?? 0)}
            />
            <MetricCard
              detail={`${overview?.workflow_failed ?? 0} failed in window`}
              icon={BadgeCheck}
              label="Workflow success"
              tone="metric-green"
              value={successRate}
            />
            <MetricCard
              detail={`${overview?.render_active ?? 0} active · ${overview?.render_failed ?? 0} failed`}
              icon={Cpu}
              label="Render jobs"
              tone="metric-orange"
              value={String(overview?.render_jobs ?? 0)}
            />
            <MetricCard
              detail="Sum of recorded actual job cost"
              icon={Wallet}
              label="Render spend"
              tone="metric-violet"
              value={formatCost(overview?.spend ?? "0")}
            />
          </section>

          <div className="monitor-primary-grid">
            <section className="monitor-panel activity-chart-panel">
              <div className="monitor-panel-heading">
                <div>
                  <span className="monitor-eyebrow">WORKFLOW THROUGHPUT</span>
                  <h2>Activity over time</h2>
                </div>
              </div>
              <div className="chart-summary">
                <span>
                  <strong>{overview?.workflow_runs ?? 0}</strong>
                  <small>runs in {range.label}</small>
                </span>
              </div>
              {overview ? (
                <ActivityChart hours={range.hours} series={overview.series} />
              ) : (
                <NotConnected
                  title={loading ? "Loading" : "No data"}
                  detail="Workflow activity will be charted once it loads."
                />
              )}
            </section>

            <section className="monitor-panel system-panel">
              <div className="monitor-panel-heading">
                <div>
                  <span className="monitor-eyebrow">CONTROL + RENDER</span>
                  <h2>Service health</h2>
                </div>
                <Server size={18} className="panel-heading-icon" />
              </div>
              <div className="mini-health-list">
                <div>
                  <span><Server size={14} /> Control plane API</span>
                  <HealthPill tone={apiHealthy ? "healthy" : "error"}>
                    {apiHealthy ? "Responding" : "Unreachable"}
                  </HealthPill>
                </div>
                <div>
                  <span><Database size={14} /> PostgreSQL</span>
                  <HealthPill tone={apiHealthy ? "healthy" : "error"}>
                    {apiHealthy ? "Queried" : "Unknown"}
                  </HealthPill>
                </div>
                <div>
                  <span><Cloud size={14} /> RunPod render plane</span>
                  <HealthPill tone="neutral">
                    {overview?.render_active ? `${overview.render_active} active jobs` : "No active jobs"}
                  </HealthPill>
                </div>
              </div>
              <div className="control-plane-note">
                <ShieldCheck size={13} />
                Health is derived from this page&apos;s own API calls. RunPod and
                scheduler probes are not connected.
              </div>
            </section>
          </div>

          <div className="monitor-secondary-grid">
            <div className="monitor-column">
              <div id="gpu-jobs">
                <JobsPanel jobs={overview?.recent_jobs ?? []} />
              </div>
              <section className="monitor-panel agent-panel">
                <div className="monitor-panel-heading">
                  <div>
                    <span className="monitor-eyebrow">AUTONOMOUS WORKFLOWS</span>
                    <h2>Agent activity</h2>
                  </div>
                </div>
                <NotConnected
                  title="Agent telemetry not connected"
                  detail="Workflow run totals above are real; per-agent status is not recorded yet."
                />
              </section>
            </div>
            <div className="monitor-column">
              <div id="costs">
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
                      <span>Recorded spend · {range.label}</span>
                      <strong>{formatCost(overview?.spend ?? "0")}</strong>
                    </div>
                  </div>
                  <div className="cost-stats">
                    <div>
                      <span>Failed job cost</span>
                      <strong>{formatCost(overview?.failed_spend ?? "0")}</strong>
                    </div>
                    <div>
                      <span>Queued</span>
                      <strong>{overview?.render_queue.queued ?? 0}</strong>
                    </div>
                    <div>
                      <span>Running</span>
                      <strong>{overview?.render_queue.running ?? 0}</strong>
                    </div>
                  </div>
                  <p className="panel-footnote">
                    <Wallet size={14} />
                    Costs are the actual_cost values stored per render job; no budget or
                    billing provider is connected.
                  </p>
                </section>
              </div>
              <div id="failures">
                <FailurePanel failures={overview?.failures ?? []} />
              </div>
              <section className="monitor-panel storage-panel">
                <div className="monitor-panel-heading">
                  <div>
                    <span className="monitor-eyebrow">LOCAL RESOURCES</span>
                    <h2>Storage &amp; capacity</h2>
                  </div>
                  <HardDrive size={18} className="panel-heading-icon" />
                </div>
                <NotConnected
                  title="Storage metrics not connected"
                  detail="Disk and object-storage usage are not measured by the API yet."
                />
              </section>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

export function MonitoringDashboard() {
  return <KeycloakGate>{(keycloak) => <MonitoringContent keycloak={keycloak} />}</KeycloakGate>;
}
