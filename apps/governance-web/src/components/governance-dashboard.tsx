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
  CalendarDays,
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
import { useMemo, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  initialTopics,
  navigationGroups,
  sectionDetails,
  type ClaimStatus,
  type Topic,
  type WorkspaceSection,
} from "@/lib/governance-data";

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

type TopicFilter = "All topics" | "Needs attention" | "Has 2+ sources";

const topicFilters: TopicFilter[] = [
  "All topics",
  "Needs attention",
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
        <strong>Preview workspace</strong>
        <span>
          Sample content only. Topics and decisions stay in this browser session;
          research, production, and governance APIs are not connected.
        </span>
      </p>
      <span className="preview-label">DEMO MODE</span>
    </div>
  );
}

function Sidebar({
  activeSection,
  onNavigate,
}: {
  activeSection: WorkspaceSection;
  onNavigate: (section: WorkspaceSection) => void;
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
                  {item.count !== undefined && (
                    <span className={`nav-count${active ? " is-active" : ""}`}>
                      {item.count}
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
        <div className="weekly-card">
          <div className="weekly-card-top">
            <span className="weekly-icon">
              <CalendarDays size={15} />
            </span>
            <span>THIS WEEK</span>
            <span className="weekly-dot" />
          </div>
          <strong>One story in review</strong>
          <span>Next publish · Tuesday, 27 Oct</span>
          <div className="weekly-track">
            <span />
          </div>
          <small>1 of 2 episodes ready</small>
        </div>
        <button className="help-link" type="button" onClick={() => onNavigate("reports")}>
          <ChartNoAxesCombined size={16} />
          <span>View governance reports</span>
          <ArrowUpRight size={14} />
        </button>
        <div className="profile-row">
          <span className="profile-avatar">ED</span>
          <span className="profile-copy">
            <strong>Editorial team</strong>
            <span>Editor access</span>
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
}: {
  search: string;
  onSearch: (value: string) => void;
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

function ReviewPanel({
  topic,
  onDecision,
  notice,
  independenceConfirmed,
  onIndependenceChange,
}: {
  topic: Topic;
  onDecision: (status: ClaimStatus) => void;
  notice: string;
  independenceConfirmed: boolean;
  onIndependenceChange: (confirmed: boolean) => void;
}) {
  const isReviewed = topic.claimStatus === "Verified";

  return (
    <aside className="review-panel">
      <div className="review-panel-heading">
        <div>
          <span className="panel-eyebrow">CLAIM REVIEW</span>
          <h2>Evidence at a glance</h2>
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
          <strong>{topic.subtitle}</strong>
          <small>{topic.category}</small>
        </span>
      </div>

      <div className="claim-block">
        <div className="claim-label">
          <span>FACTUAL CLAIM</span>
          <span className={`status-pill ${statusTone(topic.claimStatus)}`}>{topic.claimStatus}</span>
        </div>
        <blockquote>“{topic.claim}”</blockquote>
        <p>{topic.claimContext}</p>
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
          <span>Research sources will appear here when added.</span>
        </div>
      )}

      <label className="independence-check">
        <input
          checked={independenceConfirmed}
          disabled={topic.evidence.length < 2}
          onChange={(event) => onIndependenceChange(event.target.checked)}
          type="checkbox"
        />
        <span>I checked these sources are independent.</span>
      </label>

      <div className="independence-note">
        <ShieldCheck size={15} />
        <span>
          Source independence must be confirmed by a reviewer. A source count
          alone does not establish independent evidence.
        </span>
      </div>

      <div className="review-actions">
        <Button
          className="approve-button"
          disabled={isReviewed || topic.evidence.length < 2 || !independenceConfirmed}
          onClick={() => onDecision("Verified")}
          type="button"
        >
          <Check size={16} />
          {isReviewed ? "Claim verified" : "Approve claim"}
        </Button>
        <Button
          className="follow-up-button"
          disabled={topic.claimStatus === "Follow-up"}
          onClick={() => onDecision("Follow-up")}
          type="button"
          variant="outline"
        >
          <FileSearch size={15} />
          Request follow-up
        </Button>
      </div>
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
}: {
  onClose: () => void;
  onCreate: (title: string, category: string) => void;
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
            Any topic is accepted. This preview only adds it to this browser
            session; research and video generation are not connected.
          </div>
          <div className="dialog-actions">
            <Button onClick={onClose} type="button" variant="outline">
              Cancel
            </Button>
            <Button className="approve-button" type="submit">
              <CirclePlus size={16} />
              Add video idea
            </Button>
          </div>
        </form>
      </section>
    </div>
  );
}

function SecondaryWorkspace({
  section,
  topics,
  onOpenFactReview,
  approvalDecisions,
  approvalNotice,
  onApprovalDecision,
}: {
  section: WorkspaceSection;
  topics: Topic[];
  onOpenFactReview: () => void;
  approvalDecisions: Record<string, "Approved" | "Changes requested">;
  approvalNotice: string;
  onApprovalDecision: (
    title: string,
    decision: "Approved" | "Changes requested",
  ) => void;
}) {
  if (section === "approvals") {
    const approvalItems = [
      { title: "Aditya-L1 · Story outline", stage: "Outline approval", age: "Due today" },
      { title: "Chandrayaan-3 · Evidence set", stage: "Fact review", age: "Due today" },
      { title: "Compound interest · Video brief", stage: "Outline approval", age: "Due tomorrow" },
    ];

    return (
      <section className="secondary-card approval-worklist">
        <div className="secondary-heading">
          <div>
            <span className="panel-eyebrow">REVIEW GATES</span>
            <h2>Waiting for a decision</h2>
          </div>
          <span className="quiet-count">3 items</span>
        </div>
        {approvalItems.map((item, index) => {
          const decision = approvalDecisions[item.title];
          return (
            <div className="approval-row" key={item.title}>
              <span className={`approval-index approval-index-${index + 1}`}>0{index + 1}</span>
              <span className="approval-copy">
                <strong>{item.title}</strong>
                <span>{item.stage}</span>
              </span>
              <span className="approval-due">
                <Clock3 size={14} />
                {item.age}
              </span>
              {decision ? (
                <span
                  className={`status-pill ${
                    decision === "Approved" ? "status-positive" : "status-warm"
                  }`}
                >
                  {decision}
                </span>
              ) : (
                <span className="approval-actions">
                  {item.stage === "Fact review" ? (
                    <button
                      className="approval-action approval-approve"
                      onClick={onOpenFactReview}
                      type="button"
                    >
                      Review evidence
                    </button>
                  ) : (
                    <>
                      <button
                        className="approval-action approval-request"
                        onClick={() => onApprovalDecision(item.title, "Changes requested")}
                        type="button"
                      >
                        Request changes
                      </button>
                      <button
                        className="approval-action approval-approve"
                        onClick={() => {
                          onApprovalDecision(item.title, "Approved");
                        }}
                        type="button"
                      >
                        Approve
                      </button>
                    </>
                  )}
                </span>
              )}
            </div>
          );
        })}
        {approvalNotice && (
          <p className="review-notice approval-notice" role="status">
            <CheckCheck size={15} />
            {approvalNotice} No API request was made.
          </p>
        )}
        <p className="workspace-disclaimer">
          Approval controls are preview-only. The API and authenticated reviewer
          identity are not connected yet.
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
            { icon: Globe2, title: "Research sources", detail: `${topics[0]?.sources ?? 0} linked references` },
            { icon: BadgeCheck, title: "Reviewed claims", detail: "Claim status tracked per revision" },
            { icon: Clapperboard, title: "Episode package", detail: "Script and storyboard lineage" },
            { icon: FileCheck2, title: "Release assets", detail: "Final media and source manifest" },
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
          This is a UI preview of the approved lineage model. Live source and
          revision links will be supplied by the API.
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
          {[
            ["09:42", "Evidence attached", "Ananya linked 2 source references to Aditya-L1."],
            ["09:18", "Research updated", "Rohan added notes to the sourdough topic."],
            ["Yesterday", "Video brief updated", "Meera refined a personal-finance outline."],
          ].map(([time, title, detail]) => (
            <div className="audit-row" key={`${time}-${title}`}>
              <span className="audit-time">{time}</span>
              <span className="audit-marker" />
              <span className="audit-copy">
                <strong>{title}</strong>
                <span>{detail}</span>
              </span>
            </div>
          ))}
        </div>
        <p className="workspace-disclaimer">
          Sample audit entries are illustrative. Production records are
          append-only and persisted server-side.
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
          { label: "Topics with 2+ sources", value: 82, count: "9 of 11", color: "bar-saffron" },
          { label: "Claims reviewed", value: 68, count: "17 of 25", color: "bar-teal" },
          { label: "Episodes with approved outline", value: 50, count: "2 of 4", color: "bar-blue" },
        ].map((item) => (
          <div className="report-bar-row" key={item.label}>
            <span className="report-label">{item.label}</span>
            <span className="report-count">{item.count}</span>
            <span className="report-track">
              <span className={item.color} style={{ width: `${item.value}%` }} />
            </span>
            <span className="report-percent">{item.value}%</span>
          </div>
        ))}
        <p className="workspace-disclaimer">
          Preview metrics use example values. Live reports will be calculated
          from persisted workflow and review records.
        </p>
      </section>
    );
  }

  return null;
}

export function GovernanceDashboard() {
  const [activeSection, setActiveSection] = useState<WorkspaceSection>("overview");
  const [topics, setTopics] = useState(initialTopics);
  const [selectedId, setSelectedId] = useState(initialTopics[0].id);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<TopicFilter>("All topics");
  const [notice, setNotice] = useState("");
  const [showNewTopic, setShowNewTopic] = useState(false);
  const [independenceConfirmed, setIndependenceConfirmed] = useState(false);
  const [approvalDecisions, setApprovalDecisions] = useState<
    Record<string, "Approved" | "Changes requested">
  >({});
  const [approvalNotice, setApprovalNotice] = useState("");

  const selectedTopic = topics.find((topic) => topic.id === selectedId) ?? topics[0];
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
        (filter === "Needs attention" && topic.claimStatus !== "Verified") ||
        (filter === "Has 2+ sources" && topic.sources >= 2);
      return matchesSearch && matchesFilter && sectionTopicFilter(activeSection, topic);
    });
  }, [activeSection, filter, search, topics]);
  const details = sectionDetails[activeSection];

  function recordDecision(status: ClaimStatus) {
    setTopics((currentTopics) =>
      currentTopics.map((topic) =>
        topic.id === selectedTopic.id
          ? {
              ...topic,
              claimStatus: status,
              status: status === "Verified" ? "Ready to plan" : topic.status,
            }
          : topic,
      ),
    );
    setNotice(
      status === "Verified"
        ? "Demo decision noted locally. The API is not connected."
        : "Follow-up request noted locally. The API is not connected.",
    );
  }

  function createTopic(title: string, category: string) {
    const newTopic: Topic = {
      id: `topic-${Date.now()}`,
      title,
      subtitle: "New idea · research not started",
      category: category.trim() || "General",
      status: "Researching",
      claimStatus: "Needs review",
      sources: 0,
      updated: "Just now",
      owner: "Editorial team",
      priority: "Medium",
      claim: "A factual claim has not been drafted yet.",
      claimContext: "Research and evidence must be added before fact review.",
      evidence: [],
    };
    setTopics((currentTopics) => [newTopic, ...currentTopics]);
    setSelectedId(newTopic.id);
    setActiveSection("topics");
    setSearch("");
    setFilter("All topics");
    setIndependenceConfirmed(false);
    setNotice("Video idea added to this browser session only.");
    setShowNewTopic(false);
  }

  return (
    <div className="dashboard-shell">
      <Sidebar activeSection={activeSection} onNavigate={setActiveSection} />
      <div className="dashboard-main">
        <TopBar onSearch={setSearch} search={search} />

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
                <span className="page-date">Monday, 26 October 2026</span>
              </div>
              <h1>{details.title}</h1>
              <p>{details.description}</p>
            </div>
            <Button
              className="new-topic-button"
              onClick={() => setShowNewTopic(true)}
              type="button"
            >
              <CirclePlus size={17} />
              New topic
            </Button>
          </div>

          <PreviewBanner />

          <section aria-label="Editorial overview" className="metric-grid">
            <MetricCard
              icon={Orbit}
              label="Topics in queue"
              note="Across 6 learning themes"
              tone="orange"
              trend="up"
              value="12"
            />
            <MetricCard
              icon={FileSearch}
              label="Claims to review"
              note="Across 4 research briefs"
              tone="blue"
              trend="down"
              value="07"
            />
            <MetricCard
              icon={Clapperboard}
              label="Episodes in progress"
              note="Next publish · Tue, 27 Oct"
              tone="green"
              value="03"
            />
            <MetricCard
              icon={ShieldCheck}
              label="Ready for approval"
              note="One item needs your attention"
              tone="violet"
              value="02"
            />
          </section>

          <div className="workspace-grid">
            <section className="queue-panel">
              <div className="queue-heading">
                <div>
                  <span className="panel-eyebrow">
                    {activeSection === "fact-check" ? "CLAIM REVIEW QUEUE" : "EDITORIAL PIPELINE"}
                  </span>
                  <h2>
                    {activeSection === "fact-check"
                      ? "Claims needing a closer look"
                      : activeSection === "research"
                        ? "Research in motion"
                        : activeSection === "episodes"
                          ? "Stories moving forward"
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
                      {topicFilter === "Needs attention" && <span className="filter-dot" />}
                      {topicFilter}
                    </button>
                  ))}
                </div>
                <button
                  className="filter-button"
                  onClick={() => setFilter(filter === "All topics" ? "Needs attention" : "All topics")}
                  type="button"
                >
                  <ListFilter size={15} />
                  Filter
                </button>
              </div>

              <div className="topic-list">
                <div className="topic-list-head">
                  <span>TOPIC</span>
                  <span>LEARNING THEME</span>
                  <span>EVIDENCE</span>
                  <span>STAGE</span>
                  <span>UPDATED</span>
                  <span aria-hidden="true" />
                </div>
                {visibleTopics.length > 0 ? (
                  visibleTopics.map((topic) => (
                    <TopicRow
                      key={topic.id}
                      onSelect={() => {
                        setSelectedId(topic.id);
                        setNotice("");
                                  setIndependenceConfirmed(false);
                                }}
                      selected={selectedId === topic.id}
                      topic={topic}
                    />
                  ))
                ) : (
                  <div className="empty-queue">
                    <FileSearch size={21} />
                    <strong>No topics match this view</strong>
                    <span>Try another filter or search term.</span>
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

            <ReviewPanel
              key={selectedTopic.id}
              notice={notice}
              onDecision={recordDecision}
              independenceConfirmed={independenceConfirmed}
              onIndependenceChange={setIndependenceConfirmed}
              topic={selectedTopic}
            />
          </div>

          {activeSection !== "overview" && (
            <SecondaryWorkspace
              approvalDecisions={approvalDecisions}
              approvalNotice={approvalNotice}
              onApprovalDecision={(title, decision) => {
                setApprovalDecisions((current) => ({ ...current, [title]: decision }));
                setApprovalNotice(
                  `${title}: ${
                    decision === "Approved" ? "approval noted" : "changes requested"
                  } in this preview.`,
                );
              }}
              onOpenFactReview={() => {
                setSelectedId("chandrayaan-3");
                setActiveSection("fact-check");
                setIndependenceConfirmed(false);
                setNotice("");
              }}
              section={activeSection}
              topics={topics}
            />
          )}

          <div className="bottom-grid">
            <section className="bottom-card publishing-card">
              <div className="bottom-card-heading">
                <div>
                  <span className="panel-eyebrow">PUBLISHING RHYTHM</span>
                  <h2>Video release schedule</h2>
                </div>
                <button
                  className="icon-button subtle-icon-button"
                  type="button"
                  aria-label="Open episode queue"
                  onClick={() => setActiveSection("episodes")}
                >
                  <ArrowUpRight size={16} />
                </button>
              </div>
              <div className="publish-week">
                {[
                  { day: "TUE", date: "20", label: "The Moon's quiet side", state: "Published", done: true },
                  { day: "FRI", date: "23", label: "What makes sourdough rise?", state: "Published", done: true },
                  { day: "TUE", date: "27", label: "Compound interest, simply explained", state: "In review", done: false },
                ].map((item) => (
                  <div className="publish-item" key={item.label}>
                    <span className="publish-date">
                      <small>{item.day}</small>
                      <strong>{item.date}</strong>
                    </span>
                    <span className="publish-line" />
                    <span className="publish-copy">
                      <strong>{item.label}</strong>
                      <small>{item.state}</small>
                    </span>
                    {item.done ? (
                      <span className="publish-check">
                        <Check size={13} />
                      </span>
                    ) : (
                      <span className="publish-pending" />
                    )}
                  </div>
                ))}
              </div>
            </section>

            <section className="bottom-card activity-card">
              <div className="bottom-card-heading">
                <div>
                  <span className="panel-eyebrow">RECENT ACTIVITY</span>
                  <h2>Good work, moving forward</h2>
                </div>
                <span className="activity-live">
                  <span />
                  LIVE
                </span>
              </div>
              <div className="activity-list">
                <div className="activity-item">
                  <span className="activity-avatar activity-orange">AN</span>
                  <span>
                    <strong>Ananya added 2 sources</strong>
                    <small>Aditya-L1 · 12 min ago</small>
                  </span>
                </div>
                <div className="activity-item">
                  <span className="activity-avatar activity-blue">RO</span>
                  <span>
                    <strong>Rohan updated research notes</strong>
                    <small>What makes sourdough rise? · 48 min ago</small>
                  </span>
                </div>
                <div className="activity-item">
                  <span className="activity-avatar activity-green">ME</span>
                  <span>
                    <strong>Meera refined a video brief</strong>
                    <small>Compound interest · Yesterday</small>
                  </span>
                </div>
              </div>
              <button className="activity-link" type="button" onClick={() => setActiveSection("audit")}>
                See all activity <ArrowRight size={14} />
              </button>
            </section>
          </div>

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
        <NewTopicDialog onClose={() => setShowNewTopic(false)} onCreate={createTopic} />
      )}
    </div>
  );
}
