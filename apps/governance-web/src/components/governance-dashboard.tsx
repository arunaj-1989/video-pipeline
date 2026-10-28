"use client";

import Link from "next/link";
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BadgeCheck,
  Bell,
  BookOpenCheck,
  ChartNoAxesCombined,
  Check,
  CheckCheck,
  CirclePlus,
  Clapperboard,
  Clock3,
  FileCheck2,
  FileSearch,
  GitBranch,
  Globe2,
  LayoutDashboard,
  ListFilter,
  Menu,
  Orbit,
  Search,
  ScrollText,
  ShieldCheck,
  Sparkles,
  Stamp,
  X,
  type LucideIcon,
} from "lucide-react";
import Keycloak from "keycloak-js";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { KeycloakGate } from "@/components/keycloak-gate";
import { ClaimsWorkspace, EpisodesWorkspace } from "@/components/claims-workspace";
import { Button } from "@/components/ui/button";
import {
  navigationGroups,
  sectionDetails,
  topicFromRecord,
  type Topic,
  type WorkspaceSection,
} from "@/lib/governance-data";
import { createTopic as createTopicRequest, decideTopic, listTopics } from "@/lib/topic-api";
import {
  generateEpisodePlan,
  getGovernanceSummary,
  runResearch,
  listApprovals,
  listAuditEvents,
  type ApprovalRecord,
  type AuditEventRecord,
  type GovernanceSummary,
} from "@/lib/operations-api";

type OperationsData = {
  loading: boolean;
  summary: GovernanceSummary | null;
  summaryError: string;
  approvals: ApprovalRecord[];
  approvalsError: string;
  audit: AuditEventRecord[];
  auditError: string;
};

const emptyOperations: OperationsData = {
  loading: true,
  summary: null,
  summaryError: "",
  approvals: [],
  approvalsError: "",
  audit: [],
  auditError: "",
};

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

const iconMap: Record<string, LucideIcon> = {
  "layout-dashboard": LayoutDashboard,
  orbit: Orbit,
  "book-open-check": BookOpenCheck,
  "badge-check": BadgeCheck,
  clapperboard: Clapperboard,
  stamp: Stamp,
  "git-branch": GitBranch,
  "scroll-text": ScrollText,
  "chart-no-axes-combined": ChartNoAxesCombined,
};

type TopicFilter = "All topics" | "Not approved" | "Has 2+ sources";

const topicFilters: TopicFilter[] = [
  "All topics",
  "Not approved",
  "Has 2+ sources",
];

function statusTone(status: string) {
  if (status === "Verified" || status === "Approved" || status === "Ready to plan") {
    return "status-positive";
  }
  if (
    status === "Follow-up" ||
    status === "In review" ||
    status === "Needs review" ||
    status === "Changes requested"
  ) {
    return "status-warm";
  }
  return "status-neutral";
}

function sectionTopicFilter(section: WorkspaceSection, topic: Topic) {
  if (section === "research") return topic.status === "Researching";
  if (section === "fact-check") return topic.claimStatus !== "Verified";
  if (section === "episodes") {
    return topic.status === "Ready to plan" || topic.status === "Approved";
  }
  return true;
}

function PreviewBanner() {
  return (
    <div className="preview-banner" role="note">
      <span className="preview-sparkle">
        <Sparkles size={15} />
      </span>
      <p>
        <strong>Governance connected</strong>
        <span>
          Topics, topic decisions, research, claim review, episode planning, the episode queue,
          approvals (recording and history), the audit trail, and reports use PostgreSQL.
        </span>
      </p>
      <span className="preview-label">LIVE</span>
    </div>
  );
}

function Sidebar({
  activeSection,
  onNavigate,
  topicCount,
  canWriteTopics,
}: {
  activeSection: WorkspaceSection;
  onNavigate: (section: WorkspaceSection) => void;
  topicCount: number;
  canWriteTopics: boolean;
}) {
  return (
    <aside className="sidebar">
      <button
        className="brand-lockup"
        onClick={() => onNavigate("overview")}
        type="button"
      >
        <span className="brand-mark" aria-hidden="true">
          <Clapperboard size={22} strokeWidth={1.8} />
          <span />
        </span>
        <span className="brand-copy">
          <strong>universal video</strong>
          <span>MAKER STUDIO</span>
        </span>
      </button>

      <div className="workspace-switcher">
        <span className="workspace-avatar">UV</span>
        <span className="workspace-copy">
          <strong>Video workspace</strong>
          <span>All topics</span>
        </span>
      </div>

      <nav className="sidebar-nav" aria-label="Video workspace">
        {navigationGroups.map((group) => (
          <div className="nav-group" key={group.title}>
            <p className="nav-label">{group.title}</p>
            {group.items.map((item) => {
              const Icon = iconMap[item.icon];
              const active = activeSection === item.id;
              return (
                <button
                  aria-current={active ? "page" : undefined}
                  className={`nav-item${active ? " is-active" : ""}`}
                  key={item.id}
                  onClick={() => onNavigate(item.id)}
                  type="button"
                >
                  <Icon size={17} strokeWidth={1.8} aria-hidden="true" />
                  <span>{item.label}</span>
                  {(item.id === "topics" || item.count !== undefined) && (
                    <span className={`nav-count${active ? " is-active" : ""}`}>
                      {item.id === "topics" ? topicCount : item.count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="sidebar-bottom">
        <Link className="monitoring-side-link" href="/monitoring">
          <Activity size={16} />
          <span>Platform monitoring</span>
          <ArrowUpRight size={13} />
        </Link>
        <button className="help-link" type="button" onClick={() => onNavigate("reports")}>
          <ChartNoAxesCombined size={16} />
          <span>View governance reports</span>
          <ArrowUpRight size={14} />
        </button>
        <div className="profile-row">
          <span className="profile-avatar">ED</span>
          <span className="profile-copy">
            <strong>Signed-in user</strong>
            <span>{canWriteTopics ? "Editor access" : "Read-only access"}</span>
          </span>
          <span className="icon-button profile-menu" aria-hidden="true">
            <Menu size={17} />
          </span>
        </div>
      </div>
    </aside>
  );
}

function TopBar({
  search,
  onSearch,
  onLogout,
}: {
  search: string;
  onSearch: (value: string) => void;
  onLogout: () => void;
}) {
  return (
    <header className="topbar">
      <div className="breadcrumb">
        <span>Universal Video Maker</span>
        <span className="breadcrumb-slash">/</span>
        <strong>Studio</strong>
      </div>
      <div className="topbar-actions">
        <label className="search-box">
          <Search size={16} aria-hidden="true" />
          <input
            aria-label="Search topics, claims and episodes"
            onChange={(event) => onSearch(event.target.value)}
            placeholder="Search anything..."
            value={search}
          />
        </label>
        <span className="topbar-divider" />
        <span className="icon-button notification-button" role="img" aria-label="Notifications">
          <Bell size={18} />
          <span />
        </span>
        <span className="topbar-avatar" aria-label="Editorial team">
          ED
        </span>
        <Button onClick={onLogout} type="button" variant="outline">
          Sign out
        </Button>
      </div>
    </header>
  );
}

function MetricCard({
  label,
  value,
  note,
  icon: Icon,
  tone,
  trend,
}: {
  label: string;
  value: string;
  note: string;
  icon: LucideIcon;
  tone: "orange" | "blue" | "green" | "violet";
  trend?: "up" | "down";
}) {
  return (
    <article className="metric-card">
      <div className={`metric-icon metric-${tone}`}>
        <Icon size={18} strokeWidth={1.8} />
      </div>
      {trend && (
        <span className={`metric-trend${trend === "down" ? " trend-down" : ""}`}>
          {trend === "up" ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
          {trend === "up" ? "+12%" : "−8%"}
        </span>
      )}
      <span className="metric-label">{label}</span>
      <strong className="metric-value">{value}</strong>
      <span className="metric-note">{note}</span>
    </article>
  );
}

function TopicRow({
  topic,
  selected,
  onSelect,
}: {
  topic: Topic;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      aria-pressed={selected}
      className={`topic-row${selected ? " topic-row-selected" : ""}`}
      onClick={onSelect}
      type="button"
    >
      <span className="topic-main">
        <span className={`topic-avatar topic-avatar-${topic.category.length % 4}`}>
          {topic.title.slice(0, 1)}
        </span>
        <span className="topic-title-wrap">
          <strong>{topic.title}</strong>
          <span>{topic.subtitle}</span>
        </span>
      </span>
      <span className="topic-category">{topic.category}</span>
      <span className="topic-evidence">
        <span className={`evidence-dot ${topic.sources >= 2 ? "evidence-ok" : ""}`} />
        {topic.sources} sources
      </span>
      <span className={`status-pill ${statusTone(topic.status)}`}>{topic.status}</span>
      <span className="topic-updated">{topic.updated}</span>
      <ArrowRight className="topic-arrow" size={15} aria-hidden="true" />
    </button>
  );
}

const activityStages = {
  research: [
    "Searching the web and Indian government sources...",
    "Capturing source snapshots...",
    "Drafting claims with the local AI model...",
    "Saving research to PostgreSQL...",
  ],
  plan: [
    "Indexing verified evidence...",
    "Retrieving relevant evidence...",
    "Writing the outline and short concepts with the local AI model...",
    "Validating citations and saving the draft...",
  ],
} as const;

type Activity = keyof typeof activityStages;

function ProgressStatus({ activity }: { activity: Activity }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const stages = activityStages[activity];
  const stage = stages[Math.min(Math.floor(seconds / 15), stages.length - 1)];
  return (
    <div aria-live="polite" className="progress-status" role="status">
      <div className="progress-bar" aria-hidden="true">
        <span />
      </div>
      <p>{stage}</p>
      <small>
        {seconds}s elapsed. This runs on your machine and can take a few minutes; keep this page open.
      </small>
    </div>
  );
}

function ReviewPanel({
  topic,
  onDecision,
  onPlan,
  onResearch,
  notice,
  canDecide,
  busy,
  activity,
}: {
  topic: Topic;
  onDecision: (decision: "approve" | "reject", note: string) => void;
  onPlan: () => void;
  onResearch: () => void;
  notice: string;
  canDecide: boolean;
  busy: boolean;
  activity: Activity | null;
}) {
  const [note, setNote] = useState("");
  const pending = topic.status === "In review";
  return (
    <aside className="review-panel">
      <div className="review-panel-heading">
        <div>
          <span className="panel-eyebrow">PERSISTED TOPIC</span>
          <h2>Topic details</h2>
        </div>
        <span className="icon-button subtle-icon-button" aria-hidden="true">
          <Menu size={17} />
        </span>
      </div>

      <div className="review-topic">
        <span className="review-topic-icon">
          <FileCheck2 size={17} />
        </span>
        <span>
          <strong>{topic.title}</strong>
          <small>{topic.category}</small>
        </span>
      </div>

      <div className="claim-block">
        <div className="claim-label">
          <span>TOPIC STATUS</span>
          <span className={`status-pill ${statusTone(topic.status)}`}>{topic.status}</span>
        </div>
        <p>
          {pending
            ? "This topic is waiting for an editorial decision."
            : topic.status === "Rejected"
              ? "This topic was rejected."
              : "This topic has been through editorial review."}
        </p>
      </div>

      <div className="source-heading">
        <span>Supporting sources</span>
        <span>{topic.sources} linked</span>
      </div>
      {topic.evidence.length > 0 ? (
        <div className="source-list">
          {topic.evidence.map((source) => (
            <a
              className="source-row"
              href={source.url}
              key={`${topic.id}-${source.publisher}`}
              rel="noreferrer"
              target="_blank"
            >
              <span className="source-icon">
                <Globe2 size={15} />
              </span>
              <span className="source-copy">
                <strong>{source.publisher}</strong>
                <span>{source.title}</span>
              </span>
              <ArrowUpRight size={14} className="source-open" />
            </a>
          ))}
        </div>
      ) : (
        <div className="empty-evidence">
          <FileSearch size={17} />
          <span>
            {topic.sources === 0
              ? "No research sources are linked to this topic yet."
              : `${topic.sources} sources are linked; source details are not loaded in this view.`}
          </span>
        </div>
      )}

      {pending && canDecide && (
        <>
          <label className="independence-check">
            <span>Decision note (required)</span>
          </label>
          <textarea
            aria-label="Decision note"
            className="decision-note"
            maxLength={2000}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Why are you approving or rejecting this topic?"
            rows={3}
            value={note}
          />
          <div className="review-actions">
            <Button
              className="approve-button"
              disabled={busy || !note.trim()}
              onClick={() => onDecision("approve", note.trim())}
              type="button"
            >
              <Check size={16} />
              Approve topic
            </Button>
            <Button
              className="follow-up-button"
              disabled={busy || !note.trim()}
              onClick={() => onDecision("reject", note.trim())}
              type="button"
              variant="outline"
            >
              <X size={15} />
              Reject topic
            </Button>
          </div>
        </>
      )}
      {pending && !canDecide && (
        <div className="independence-note">
          <ShieldCheck size={15} />
          <span>Only editors and admins can approve or reject topics.</span>
        </div>
      )}
      {topic.status === "Ready to plan" && canDecide && (
        <div className="review-actions">
          <Button
            className="follow-up-button"
            disabled={busy}
            onClick={onResearch}
            type="button"
            variant="outline"
          >
            <FileSearch size={15} />
            {activity === "research" ? "Researching..." : "Run research"}
          </Button>
          <Button className="approve-button" disabled={busy} onClick={onPlan} type="button">
            <Sparkles size={16} />
            {activity === "plan" ? "Planning..." : "Plan episode"}
          </Button>
        </div>
      )}
      {activity && <ProgressStatus activity={activity} key={activity} />}
      {notice && (
        <p className="review-notice" role="status">
          <CheckCheck size={15} />
          {notice}
        </p>
      )}
    </aside>
  );
}

function NewTopicDialog({
  onClose,
  onCreate,
  saving,
}: {
  onClose: () => void;
  onCreate: (title: string, category: string) => void;
  saving: boolean;
}) {
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!title.trim()) return;
    onCreate(title.trim(), category);
  }

  return (
    <div className="dialog-backdrop" onClick={onClose} role="presentation">
      <section
        aria-labelledby="new-topic-title"
        aria-modal="true"
        className="new-topic-dialog"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className="dialog-heading">
          <div>
            <span className="panel-eyebrow">TOPIC INTAKE</span>
            <h2 id="new-topic-title">Start a video idea</h2>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close dialog">
            <X size={18} />
          </button>
        </div>
        <p className="dialog-copy">
          Enter a topic from any subject. Add a category if it helps organize
          your queue.
        </p>
        <form className="topic-form" onSubmit={handleSubmit}>
          <label>
            Video topic
            <input
              autoFocus
              maxLength={300}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="e.g. How does compound interest work?"
              required
              value={title}
            />
          </label>
          <label>
            Category (optional)
            <input
              maxLength={80}
              onChange={(event) => setCategory(event.target.value)}
              placeholder="Science, history, cooking, finance..."
              value={category}
            />
          </label>
          <div className="dialog-demo-note">
            <Sparkles size={15} />
            This topic will be saved to the shared PostgreSQL database. Research
            and video generation are not connected yet.
          </div>
          <div className="dialog-actions">
            <Button onClick={onClose} type="button" variant="outline">
              Cancel
            </Button>
            <Button className="approve-button" disabled={saving} type="submit">
              <CirclePlus size={16} />
              {saving ? "Saving..." : "Add video idea"}
            </Button>
          </div>
        </form>
      </section>
    </div>
  );
}

function formatTimestamp(value: string) {
  return new Date(value).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function percentage(part: number, whole: number) {
  return whole === 0 ? 0 : Math.round((part / whole) * 100);
}

function EmptyWorkspace({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-queue">
      <FileSearch size={21} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function SecondaryWorkspace({
  section,
  ops,
}: {
  section: WorkspaceSection;
  ops: OperationsData;
}) {
  const { summary, approvals, audit, loading } = ops;

  if (loading) {
    return (
      <section className="secondary-card" role="status">
        Loading records from PostgreSQL...
      </section>
    );
  }

  if (section === "approvals") {
    return (
      <section className="secondary-card approval-worklist">
        <div className="secondary-heading">
          <div>
            <span className="panel-eyebrow">REVIEW GATES</span>
            <h2>Recorded approval decisions</h2>
          </div>
          <span className="quiet-count">{approvals.length} records</span>
        </div>
        {ops.approvalsError ? (
          <EmptyWorkspace title="Approvals unavailable" detail={ops.approvalsError} />
        ) : approvals.length === 0 ? (
          <EmptyWorkspace
            title="No approval decisions yet"
            detail="Decisions will appear here once an episode revision is reviewed."
          />
        ) : (
          approvals.map((item, index) => (
            <div className="approval-row" key={item.id}>
              <span className={`approval-index approval-index-${(index % 3) + 1}`}>
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="approval-copy">
                <strong>
                  {item.episode_title} · revision {item.revision}
                </strong>
                <span>
                  {item.stage}
                  {item.note ? ` · ${item.note}` : ""}
                </span>
              </span>
              <span className="approval-due">
                <Clock3 size={14} />
                {formatTimestamp(item.created_at)}
              </span>
              <span
                className={`status-pill ${
                  item.decision === "approved" ? "status-positive" : "status-warm"
                }`}
              >
                {item.decision}
              </span>
            </div>
          ))
        )}
        <p className="workspace-disclaimer">
          Read-only history from the approvals table. Decisions are recorded from the episode
          queue by opening an episode plan.
        </p>
      </section>
    );
  }

  if (section === "lineage") {
    return (
      <section className="secondary-card lineage-card">
        <div className="secondary-heading">
          <div>
            <span className="panel-eyebrow">SOURCE TO STORY</span>
            <h2>Trace a content thread</h2>
          </div>
          <GitBranch size={18} className="lineage-heading-icon" />
        </div>
        <div className="lineage-flow">
          {[
            { icon: Globe2, title: "Research sources", detail: `${summary?.sources_linked ?? 0} linked references` },
            { icon: BadgeCheck, title: "Reviewed claims", detail: `${summary?.claims_reviewed ?? 0} of ${summary?.claims_total ?? 0} claims reviewed` },
            { icon: Clapperboard, title: "Episode package", detail: `${summary?.episodes_total ?? 0} episodes, ${summary?.episodes_with_approval ?? 0} with an approval` },
            { icon: FileCheck2, title: "Approval records", detail: `${summary?.approvals_total ?? 0} recorded decisions` },
          ].map((step, index) => {
            const Icon = step.icon;
            return (
              <div className="lineage-step" key={step.title}>
                <span className={`lineage-node${index === 0 ? " lineage-node-current" : ""}`}>
                  <Icon size={17} />
                </span>
                <span className="lineage-step-copy">
                  <strong>{step.title}</strong>
                  <span>{step.detail}</span>
                </span>
                {index < 3 && <span className="lineage-connector" />}
              </div>
            );
          })}
        </div>
        <p className="workspace-disclaimer">
          Counts come from persisted records. Per-item lineage tracing is not
          exposed by the API yet.
        </p>
      </section>
    );
  }

  if (section === "audit") {
    return (
      <section className="secondary-card">
        <div className="secondary-heading">
          <div>
            <span className="panel-eyebrow">RECENT ACTIVITY</span>
            <h2>Governance event trail</h2>
          </div>
          <ScrollText size={18} className="lineage-heading-icon" />
        </div>
        <div className="audit-list">
          {ops.auditError ? (
            <EmptyWorkspace title="Audit trail unavailable" detail={ops.auditError} />
          ) : audit.length === 0 ? (
            <EmptyWorkspace
              title="No audit events recorded"
              detail="Append-only events will appear here as governed actions occur."
            />
          ) : (
            audit.map((event) => (
              <div className="audit-row" key={event.id}>
                <span className="audit-time">{formatTimestamp(event.created_at)}</span>
                <span className="audit-marker" />
                <span className="audit-copy">
                  <strong>{event.action}</strong>
                  <span>
                    {event.object_type}
                    {event.object_id ? ` · ${event.object_id}` : ""}
                  </span>
                </span>
              </div>
            ))
          )}
        </div>
        <p className="workspace-disclaimer">
          Append-only records read from PostgreSQL. Visible to administrators only.
        </p>
      </section>
    );
  }

  if (section === "reports") {
    return (
      <section className="secondary-card report-card">
        <div className="secondary-heading">
          <div>
            <span className="panel-eyebrow">EDITORIAL HEALTH</span>
            <h2>Evidence coverage snapshot</h2>
          </div>
          <ChartNoAxesCombined size={19} className="lineage-heading-icon" />
        </div>
        {[
          {
            label: "Topics with 2+ sources",
            part: summary?.topics_with_multiple_sources ?? 0,
            whole: summary?.topics_total ?? 0,
            color: "bar-saffron",
          },
          {
            label: "Claims reviewed",
            part: summary?.claims_reviewed ?? 0,
            whole: summary?.claims_total ?? 0,
            color: "bar-teal",
          },
          {
            label: "Episodes with an approval",
            part: summary?.episodes_with_approval ?? 0,
            whole: summary?.episodes_total ?? 0,
            color: "bar-blue",
          },
        ].map((item) => {
          const value = percentage(item.part, item.whole);
          return (
            <div className="report-bar-row" key={item.label}>
              <span className="report-label">{item.label}</span>
              <span className="report-count">
                {item.part} of {item.whole}
              </span>
              <span className="report-track">
                <span className={item.color} style={{ width: `${value}%` }} />
              </span>
              <span className="report-percent">{value}%</span>
            </div>
          );
        })}
        <p className="workspace-disclaimer">
          Calculated from persisted topic, claim, and approval records.
        </p>
      </section>
    );
  }

  return null;
}

function AuthenticatedGovernanceDashboard({ keycloak }: { keycloak: Keycloak }) {
  const [activeSection, setActiveSection] = useState<WorkspaceSection>("topics");
  const [topics, setTopics] = useState<Topic[]>([]);
  const [topicsLoading, setTopicsLoading] = useState(true);
  const [topicsError, setTopicsError] = useState("");
  const [topicSaving, setTopicSaving] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [reloadTopics, setReloadTopics] = useState(0);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<TopicFilter>("All topics");
  const [notice, setNotice] = useState("");
  const [showNewTopic, setShowNewTopic] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [ops, setOps] = useState<OperationsData>(emptyOperations);

  const selectedTopic = topics.find((topic) => topic.id === selectedId) ?? topics[0];
  const canWriteTopics = keycloak.hasRealmRole("editor") || keycloak.hasRealmRole("admin");

  useEffect(() => {
    let active = true;
    async function loadTopics() {
      setTopicsLoading(true);
      setTopicsError("");
      try {
        await keycloak.updateToken(30);
        if (!keycloak.token) throw new Error("Your session expired. Sign in again.");
        const records = await listTopics(keycloak.token);
        if (active) {
          const loadedTopics = records.map(topicFromRecord);
          setTopics(loadedTopics);
          setSelectedId(loadedTopics[0]?.id ?? "");
        }
      } catch (error) {
        if (active) {
          setTopicsError(error instanceof Error ? error.message : "Could not load topics.");
        }
      } finally {
        if (active) setTopicsLoading(false);
      }
    }

    void loadTopics();
    return () => {
      active = false;
    };
  }, [keycloak, reloadTopics]);

  useEffect(() => {
    let active = true;
    async function loadOperations() {
      setOps((current) => ({ ...current, loading: true }));
      try {
        await keycloak.updateToken(30);
        if (!keycloak.token) throw new Error("Your session expired. Sign in again.");
        const token = keycloak.token;
        const isAdmin = keycloak.hasRealmRole("admin");
        const [summary, approvals, audit] = await Promise.allSettled([
          getGovernanceSummary(token),
          listApprovals(token),
          isAdmin ? listAuditEvents(token) : Promise.resolve([] as AuditEventRecord[]),
        ]);
        if (!active) return;
        setOps({
          loading: false,
          summary: summary.status === "fulfilled" ? summary.value : null,
          summaryError:
            summary.status === "rejected"
              ? errorMessage(summary.reason, "Could not load summary.")
              : "",
          approvals: approvals.status === "fulfilled" ? approvals.value : [],
          approvalsError:
            approvals.status === "rejected"
              ? errorMessage(approvals.reason, "Could not load approvals.")
              : "",
          audit: audit.status === "fulfilled" ? audit.value : [],
          auditError:
            audit.status === "rejected"
              ? errorMessage(audit.reason, "Could not load audit events.")
              : !isAdmin
                ? "Only administrators can read the audit trail."
                : "",
        });
      } catch (error) {
        if (!active) return;
        setOps({
          ...emptyOperations,
          loading: false,
          summaryError: errorMessage(error, "Could not load governance data."),
        });
      }
    }

    void loadOperations();
    return () => {
      active = false;
    };
  }, [keycloak, reloadTopics]);

  const visibleTopics = useMemo(() => {
    const normalizedQuery = search.trim().toLowerCase();
    return topics.filter((topic) => {
      const matchesSearch =
        !normalizedQuery ||
        [topic.title, topic.subtitle, topic.category, topic.owner]
          .join(" ")
          .toLowerCase()
          .includes(normalizedQuery);
      const matchesFilter =
        filter === "All topics" ||
        (filter === "Not approved" && topic.status !== "Approved") ||
        (filter === "Has 2+ sources" && topic.sources >= 2);
      return matchesSearch && matchesFilter && sectionTopicFilter(activeSection, topic);
    });
  }, [activeSection, filter, search, topics]);
  const details = sectionDetails[activeSection];

  async function signOut() {
    try {
      await keycloak.logout({ redirectUri: window.location.origin });
    } catch (error) {
      setTopicsError(
        error instanceof Error ? `Could not sign out: ${error.message}` : "Could not sign out.",
      );
    }
  }

  async function researchTopic() {
    if (!selectedTopic) return;
    setDeciding(true);
    setActivity("research");
    setNotice("");
    setTopicsError("");
    try {
      await keycloak.updateToken(30);
      if (!keycloak.token) throw new Error("Your session expired. Sign in again.");
      const result = await runResearch(keycloak.token, selectedTopic.id);
      setNotice(
        `Research saved: ${result.claim_count} unverified claim(s) from ${result.source_count} source(s). Review them in Fact verification.`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not run research.");
    } finally {
      setDeciding(false);
      setActivity(null);
    }
  }

  async function planEpisode() {
    if (!selectedTopic) return;
    setDeciding(true);
    setActivity("plan");
    setNotice("");
    setTopicsError("");
    try {
      await keycloak.updateToken(30);
      if (!keycloak.token) throw new Error("Your session expired. Sign in again.");
      const record = await generateEpisodePlan(keycloak.token, selectedTopic.id);
      setNotice(`Episode plan revision ${record.revision} saved as a draft for review.`);
      setActiveSection("episodes");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not plan the episode.");
    } finally {
      setDeciding(false);
      setActivity(null);
    }
  }

  async function recordDecision(decision: "approve" | "reject", note: string) {
    if (!selectedTopic) return;
    setDeciding(true);
    setTopicsError("");
    try {
      await keycloak.updateToken(30);
      if (!keycloak.token) throw new Error("Your session expired. Sign in again.");
      const record = await decideTopic(keycloak.token, selectedTopic.id, { decision, note });
      const updated = topicFromRecord(record);
      setTopics((currentTopics) =>
        currentTopics.map((topic) => (topic.id === updated.id ? updated : topic)),
      );
      setNotice(
        decision === "approve"
          ? "Topic approved and recorded in the audit trail."
          : "Topic rejected and recorded in the audit trail.",
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not record the decision.");
    } finally {
      setDeciding(false);
    }
  }

  async function createTopic(title: string, category: string) {
    setTopicSaving(true);
    setTopicsError("");
    try {
      await keycloak.updateToken(30);
      if (!keycloak.token) throw new Error("Your session expired. Sign in again.");
      const record = await createTopicRequest(keycloak.token, {
        title,
        category: category.trim() || "General",
      });
      const topic = topicFromRecord(record);
      setTopics((currentTopics) => [topic, ...currentTopics]);
      setOps((current) =>
        current.summary
          ? {
              ...current,
              summary: { ...current.summary, topics_total: current.summary.topics_total + 1 },
            }
          : current,
      );
      setSelectedId(topic.id);
      setActiveSection("topics");
      setSearch("");
      setFilter("All topics");
      setShowNewTopic(false);
      setNotice("Topic saved to PostgreSQL.");
    } catch (error) {
      setTopicsError(error instanceof Error ? error.message : "Could not save topic.");
    } finally {
      setTopicSaving(false);
    }
  }

  return (
    <div className="dashboard-shell">
      <Sidebar
        activeSection={activeSection}
        onNavigate={setActiveSection}
        topicCount={topics.length}
        canWriteTopics={canWriteTopics}
      />
      <div className="dashboard-main">
        <TopBar
          onLogout={() => void signOut()}
          onSearch={setSearch}
          search={search}
        />

        <main className="dashboard-content">
          <nav aria-label="Governance sections" className="mobile-nav">
            {navigationGroups.flatMap((group) =>
              group.items.map((item) => {
                const Icon = iconMap[item.icon];
                return (
                  <button
                    aria-current={activeSection === item.id ? "page" : undefined}
                    className={`mobile-nav-button${activeSection === item.id ? " is-active" : ""}`}
                    key={item.id}
                    onClick={() => setActiveSection(item.id)}
                    type="button"
                  >
                    <Icon size={14} />
                    {item.label}
                  </button>
                );
              }),
            )}
            <Link
              className="mobile-nav-button monitoring-mobile-nav-button"
              href="/monitoring"
            >
              <Activity size={14} />
              Monitoring
            </Link>
          </nav>
          <div className="page-heading">
            <div>
              <div className="eyebrow-row">
                <span className="page-eyebrow">{details.eyebrow}</span>
                <span className="eyebrow-dot" />
                <span className="page-date">Live PostgreSQL records</span>
              </div>
              <h1>{details.title}</h1>
              <p>{details.description}</p>
            </div>
            {canWriteTopics && (
              <Button
                className="new-topic-button"
                onClick={() => setShowNewTopic(true)}
                type="button"
              >
                <CirclePlus size={17} />
                New topic
              </Button>
            )}
          </div>

          <PreviewBanner />
          {notice && (
            <p className="review-notice" role="status">
              <CheckCheck size={15} />
              {notice}
            </p>
          )}

          {activeSection === "overview" && (
            <section aria-label="Editorial overview" className="metric-grid">
            <MetricCard
              icon={Orbit}
              label="Topics in queue"
              note={`${ops.summary?.topics_approved ?? 0} approved`}
              tone="orange"
              value={String(topics.length)}
            />
            <MetricCard
              icon={FileSearch}
              label="Claims to review"
              note={`${ops.summary?.claims_verified ?? 0} verified of ${ops.summary?.claims_total ?? 0}`}
              tone="blue"
              value={String(
                (ops.summary?.claims_total ?? 0) - (ops.summary?.claims_reviewed ?? 0),
              )}
            />
            <MetricCard
              icon={Clapperboard}
              label="Episodes"
              note={`${ops.summary?.episodes_with_approval ?? 0} with an approval`}
              tone="green"
              value={String(ops.summary?.episodes_total ?? 0)}
            />
            <MetricCard
              icon={ShieldCheck}
              label="Approval decisions"
              note="Recorded across all revisions"
              tone="violet"
              value={String(ops.summary?.approvals_total ?? 0)}
            />
            </section>
          )}
          {activeSection === "overview" && ops.summaryError && (
            <p className="review-notice" role="alert">
              Could not load overview metrics: {ops.summaryError}
            </p>
          )}

          {activeSection === "fact-check" && <ClaimsWorkspace keycloak={keycloak} />}
          {activeSection === "episodes" && <EpisodesWorkspace keycloak={keycloak} />}

          {activeSection !== "fact-check" && activeSection !== "episodes" && (
          <div className="workspace-grid">
            <section className="queue-panel">
              <div className="queue-heading">
                <div>
                  <span className="panel-eyebrow">EDITORIAL PIPELINE</span>
                  <h2>
                    {activeSection === "research"
                      ? "Research in motion"
                      : activeSection === "topics"
                        ? "Explore the topic queue"
                        : "Your topic queue"}
                  </h2>
                </div>
                <Button
                  className="text-action"
                  onClick={() => {
                    setActiveSection("topics");
                    setFilter("All topics");
                  }}
                  type="button"
                  variant="ghost"
                >
                  View all
                  <ArrowRight size={15} />
                </Button>
              </div>

              <div className="queue-toolbar">
                <div aria-label="Filter topic queue" className="filter-tabs" role="group">
                  {topicFilters.map((topicFilter) => (
                    <button
                      aria-pressed={filter === topicFilter}
                      className={`filter-tab${filter === topicFilter ? " filter-tab-active" : ""}`}
                      key={topicFilter}
                      onClick={() => setFilter(topicFilter)}
                      type="button"
                    >
                      {topicFilter === "Not approved" && <span className="filter-dot" />}
                      {topicFilter}
                    </button>
                  ))}
                </div>
                <button
                  className="filter-button"
                  onClick={() => setFilter(filter === "All topics" ? "Not approved" : "All topics")}
                  type="button"
                >
                  <ListFilter size={15} />
                  Filter
                </button>
              </div>

              {topicsError && (
                <div className="empty-queue" role="alert">
                  <strong>Could not load or save topics</strong>
                  <span>{topicsError}</span>
                  <button
                    className="footer-link"
                    onClick={() => setReloadTopics((value) => value + 1)}
                    type="button"
                  >
                    Retry
                  </button>
                </div>
              )}

              <div className="topic-list">
                <div className="topic-list-head">
                  <span>TOPIC</span>
                  <span>LEARNING THEME</span>
                  <span>EVIDENCE</span>
                  <span>STAGE</span>
                  <span>UPDATED</span>
                  <span aria-hidden="true" />
                </div>
                {topicsLoading ? (
                  <div className="empty-queue" role="status">Loading topics from PostgreSQL...</div>
                ) : visibleTopics.length > 0 ? (
                  visibleTopics.map((topic) => (
                    <TopicRow
                      key={topic.id}
                      onSelect={() => {
                        setSelectedId(topic.id);
                        setNotice("");
                      }}
                      selected={selectedId === topic.id}
                      topic={topic}
                    />
                  ))
                ) : (
                  <div className="empty-queue">
                    <FileSearch size={21} />
                    <strong>No topics match this view</strong>
                    <span>
                      {topics.length === 0
                        ? "Create a topic to begin building your real workspace."
                        : "Try another filter or search term."}
                    </span>
                  </div>
                )}
              </div>
              <div className="queue-footer">
                <span>
                  Showing <strong>{visibleTopics.length}</strong> of {topics.length} topics
                </span>
                <button className="footer-link" type="button" onClick={() => setActiveSection("research")}>
                  Open research desk <ArrowRight size={14} />
                </button>
              </div>
            </section>

            {selectedTopic ? (
              <ReviewPanel
                key={selectedTopic.id}
                notice={notice}
                onDecision={recordDecision}
                onPlan={planEpisode}
                onResearch={researchTopic}
                canDecide={canWriteTopics}
                busy={deciding}
                activity={activity}
                topic={selectedTopic}
              />
            ) : (
              <aside className="review-panel">
                <h2>Topic details</h2>
                <p>Select a persisted topic to view its record.</p>
              </aside>
            )}
          </div>
          )}

          {activeSection !== "overview" &&
            activeSection !== "fact-check" &&
            activeSection !== "episodes" && (
            <SecondaryWorkspace section={activeSection} ops={ops} />
          )}

          {activeSection === "overview" && (
            <div className="bottom-grid">
            <section className="bottom-card publishing-card">
              <div className="bottom-card-heading">
                <div>
                  <span className="panel-eyebrow">RECENT DECISIONS</span>
                  <h2>Latest approvals</h2>
                </div>
                <button
                  className="icon-button subtle-icon-button"
                  type="button"
                  aria-label="Open approvals"
                  onClick={() => setActiveSection("approvals")}
                >
                  <ArrowUpRight size={16} />
                </button>
              </div>
              <div className="publish-week">
                {ops.approvals.length === 0 ? (
                  <EmptyWorkspace
                    title="No approvals yet"
                    detail="Approval decisions will appear here."
                  />
                ) : (
                  ops.approvals.slice(0, 3).map((item) => (
                    <div className="publish-item" key={item.id}>
                      <span className="publish-date">
                        <small>REV</small>
                        <strong>{item.revision}</strong>
                      </span>
                      <span className="publish-line" />
                      <span className="publish-copy">
                        <strong>{item.episode_title}</strong>
                        <small>
                          {item.stage} · {item.decision}
                        </small>
                      </span>
                      {item.decision === "approved" ? (
                        <span className="publish-check">
                          <Check size={13} />
                        </span>
                      ) : (
                        <span className="publish-pending" />
                      )}
                    </div>
                  ))
                )}
              </div>
            </section>

            <section className="bottom-card activity-card">
              <div className="bottom-card-heading">
                <div>
                  <span className="panel-eyebrow">RECENT ACTIVITY</span>
                  <h2>Newest topics</h2>
                </div>
              </div>
              <div className="activity-list">
                {topics.length === 0 ? (
                  <EmptyWorkspace
                    title="No topics yet"
                    detail="Create a topic to start the activity feed."
                  />
                ) : (
                  topics.slice(0, 3).map((topic) => (
                    <div className="activity-item" key={topic.id}>
                      <span className="activity-avatar activity-orange">
                        {topic.category.slice(0, 2).toUpperCase()}
                      </span>
                      <span>
                        <strong>{topic.title}</strong>
                        <small>
                          {topic.category} · {topic.status}
                        </small>
                      </span>
                    </div>
                  ))
                )}
              </div>
              <button className="activity-link" type="button" onClick={() => setActiveSection("audit")}>
                Open audit trail <ArrowRight size={14} />
              </button>
            </section>
            </div>
          )}

          <footer className="dashboard-footer">
            <span>Made for curious minds, reviewed with care.</span>
            <span>
              <ShieldCheck size={13} />
              Any subject · evidence-led
            </span>
          </footer>
        </main>
      </div>

      {showNewTopic && (
        <NewTopicDialog
          onClose={() => setShowNewTopic(false)}
          onCreate={(title, category) => void createTopic(title, category)}
          saving={topicSaving}
        />
      )}
    </div>
  );
}

export function GovernanceDashboard() {
  return (
    <KeycloakGate>
      {(keycloak) => <AuthenticatedGovernanceDashboard keycloak={keycloak} />}
    </KeycloakGate>
  );
}
