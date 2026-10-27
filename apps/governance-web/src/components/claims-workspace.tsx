"use client";

import { CheckCheck, FileSearch } from "lucide-react";
import type Keycloak from "keycloak-js";
import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  getEpisodePlan,
  listClaims,
  listEpisodes,
  recordPlanApproval,
  reviewClaim,
  type ApprovalDecision,
  type ClaimCategory,
  type ClaimRecord,
  type EpisodePlanRecord,
  type EpisodeRecord,
  type ReviewDecision,
} from "@/lib/operations-api";

const categories: { value: ClaimCategory; label: string }[] = [
  { value: "verified", label: "Verified fact" },
  { value: "historical_record", label: "Historical record" },
  { value: "scientific_theory", label: "Scientific theory" },
  { value: "opinion", label: "Opinion" },
  { value: "myth_or_legend", label: "Myth or legend" },
  { value: "rejected", label: "Rejected" },
];

const decisions: { value: ReviewDecision; label: string }[] = [
  { value: "verified", label: "Verify" },
  { value: "needs_follow_up", label: "Needs follow-up" },
  { value: "rejected", label: "Reject" },
];

function Empty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-queue">
      <FileSearch size={21} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function statusClass(status: string) {
  if (status === "verified") return "status-positive";
  if (status === "rejected" || status === "needs_follow_up") return "status-warm";
  return "status-neutral";
}

async function freshToken(keycloak: Keycloak) {
  await keycloak.updateToken(30);
  if (!keycloak.token) throw new Error("Your session expired. Sign in again.");
  return keycloak.token;
}

export function ClaimsWorkspace({ keycloak }: { keycloak: Keycloak }) {
  const [claims, setClaims] = useState<ClaimRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [decision, setDecision] = useState<ReviewDecision>("verified");
  const [category, setCategory] = useState<ClaimCategory>("verified");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [reload, setReload] = useState(0);

  const canReview = keycloak.hasRealmRole("fact_reviewer") || keycloak.hasRealmRole("admin");
  const selected = claims.find((claim) => claim.id === selectedId) ?? claims[0];

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const records = await listClaims(await freshToken(keycloak));
        if (active) {
          setClaims(records);
          setError("");
        }
      } catch (loadError) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Could not load claims.");
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [keycloak, reload]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected || !note.trim()) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const updated = await reviewClaim(await freshToken(keycloak), selected.id, {
        decision,
        category,
        note: note.trim(),
      });
      setClaims((current) => current.map((claim) => (claim.id === updated.id ? updated : claim)));
      setNote("");
      setMessage(`Decision recorded: ${updated.verification_status.replace("_", " ")}.`);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Could not save the review.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="workspace-grid">
      <section className="queue-panel">
        <div className="queue-heading">
          <div>
            <span className="panel-eyebrow">CLAIM REVIEW QUEUE</span>
            <h2>Claims and their evidence</h2>
          </div>
          <span className="quiet-count">{claims.length} claims</span>
        </div>
        {error && (
          <div className="empty-queue" role="alert">
            <strong>Claim request failed</strong>
            <span>{error}</span>
            <button
              className="footer-link"
              onClick={() => {
                setLoading(true);
                setReload((v) => v + 1);
              }}
              type="button"
            >
              Retry
            </button>
          </div>
        )}
        <div className="topic-list">
          {loading ? (
            <div className="empty-queue" role="status">Loading claims from PostgreSQL...</div>
          ) : claims.length === 0 ? (
            <Empty
              title="No claims recorded"
              detail="Claims appear here once research produces them."
            />
          ) : (
            claims.map((claim) => (
              <button
                className={`topic-row${selected?.id === claim.id ? " topic-row-selected" : ""}`}
                key={claim.id}
                onClick={() => {
                  setSelectedId(claim.id);
                  setMessage("");
                }}
                type="button"
              >
                <span className="topic-title-cell">
                  <strong>{claim.claim_text}</strong>
                  <small>
                    {claim.topic_title} · {claim.independent_supporting_source_count} independent
                    source(s)
                  </small>
                </span>
                <span className={`status-pill ${statusClass(claim.verification_status)}`}>
                  {claim.verification_status.replace("_", " ")}
                </span>
              </button>
            ))
          )}
        </div>
      </section>

      {selected ? (
        <aside className="review-panel">
          <span className="panel-eyebrow">EVIDENCE</span>
          <h2>{selected.topic_title}</h2>
          <div className="claim-block">
            <div className="claim-label">Claim</div>
            <p>{selected.claim_text}</p>
          </div>
          <p>
            {selected.supporting_source_count} supporting ·{" "}
            {selected.contradicting_source_count} contradicting ·{" "}
            {selected.independent_supporting_source_count} independent supporting
            {selected.has_conflicting_evidence ? " · conflicting evidence" : ""}
          </p>
          {selected.sources.length === 0 ? (
            <Empty title="No evidence linked" detail="This claim has no sources yet." />
          ) : (
            selected.sources.map((source) => (
              <div className="claim-block" key={source.source_id}>
                <div className="claim-label">
                  {source.support_type} · group {source.independence_group + 1}
                </div>
                <strong>{source.title}</strong>
                <p>{source.publisher}</p>
                <p>{source.excerpt}</p>
                <a href={source.url} rel="noreferrer noopener" target="_blank">
                  {source.url}
                </a>
              </div>
            ))
          )}
          {selected.review_note && <p>Last review note: {selected.review_note}</p>}
          {canReview ? (
            <form onSubmit={(event) => void submit(event)}>
              <label>
                Decision
                <select
                  onChange={(event) => setDecision(event.target.value as ReviewDecision)}
                  value={decision}
                >
                  {decisions.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Category
                <select
                  onChange={(event) => setCategory(event.target.value as ClaimCategory)}
                  value={category}
                >
                  {categories.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Review note
                <textarea
                  maxLength={2000}
                  onChange={(event) => setNote(event.target.value)}
                  required
                  value={note}
                />
              </label>
              <Button className="approve-button" disabled={saving || !note.trim()} type="submit">
                {saving ? "Saving..." : "Record decision"}
              </Button>
            </form>
          ) : (
            <p className="workspace-disclaimer">
              Only fact reviewers and administrators can record claim decisions.
            </p>
          )}
          {message && (
            <p className="review-notice" role="status">
              <CheckCheck size={15} />
              {message}
            </p>
          )}
        </aside>
      ) : (
        <aside className="review-panel">
          <h2>Claim details</h2>
          <p>Select a claim to review its evidence.</p>
        </aside>
      )}
    </div>
  );
}

function PlanView({ record }: { record: EpisodePlanRecord }) {
  const { plan } = record;
  return (
    <div className="plan-view">
      <p>
        <strong>{plan.title}</strong> · revision {record.revision}
      </p>
      <p>{plan.logline}</p>
      <h3>Learning objectives</h3>
      <ul>
        {plan.learning_objectives.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <h3>Story arc</h3>
      <p>{plan.story_arc.join(" → ")}</p>
      <h3>Beats ({plan.target_duration_seconds}s)</h3>
      <ol>
        {plan.beats.map((beat) => (
          <li key={beat.start_seconds}>
            <strong>
              {beat.title} ({beat.start_seconds}s, {beat.duration_seconds}s)
            </strong>{" "}
            — {beat.purpose}
            <br />
            <em>{beat.narration.text}</em>
          </li>
        ))}
      </ol>
      <h3>Shorts</h3>
      <ol>
        {plan.shorts.map((short) => (
          <li key={short.title}>
            <strong>
              {short.title} ({short.duration_seconds}s)
            </strong>{" "}
            — {short.hook}
            <br />
            <em>{short.narration.text}</em> · {short.call_to_action}
          </li>
        ))}
      </ol>
      {plan.limitations.map((item) => (
        <p className="workspace-disclaimer" key={item}>
          {item}
        </p>
      ))}
    </div>
  );
}

function ApprovalForm({
  keycloak,
  episodeId,
  revision,
  onRecorded,
}: {
  keycloak: Keycloak;
  episodeId: string;
  revision: number | null;
  onRecorded: () => void;
}) {
  const [decision, setDecision] = useState<ApprovalDecision>("approved");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const canApprove = keycloak.hasRealmRole("editor") || keycloak.hasRealmRole("admin");

  if (!canApprove) {
    return (
      <p className="workspace-disclaimer">
        Only editors and administrators can record plan approvals.
      </p>
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const saved = await recordPlanApproval(await freshToken(keycloak), episodeId, {
        decision,
        note: note.trim(),
      });
      setMessage(`Recorded "${saved.decision}" for revision ${saved.revision}.`);
      setNote("");
      onRecorded();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not record the decision.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)}>
      <h3>Review latest plan{revision ? ` (revision ${revision})` : ""}</h3>
      <label>
        Decision
        <select
          onChange={(event) => setDecision(event.target.value as ApprovalDecision)}
          value={decision}
        >
          <option value="approved">Approve</option>
          <option value="changes_requested">Request changes</option>
          <option value="rejected">Reject</option>
        </select>
      </label>
      <label>
        Review note
        <textarea
          maxLength={1000}
          onChange={(event) => setNote(event.target.value)}
          required
          value={note}
        />
      </label>
      <Button className="approve-button" disabled={saving || !note.trim()} type="submit">
        {saving ? "Saving..." : "Record decision"}
      </Button>
      {message && (
        <p className="review-notice" role="status">
          <CheckCheck size={15} />
          {message}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </form>
  );
}

export function EpisodesWorkspace({ keycloak }: { keycloak: Keycloak }) {
  const [episodes, setEpisodes] = useState<EpisodeRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState("");
  const [plan, setPlan] = useState<EpisodePlanRecord | null>(null);
  const [planError, setPlanError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  async function togglePlan(episodeId: string) {
    if (openId === episodeId) {
      setOpenId("");
      return;
    }
    setOpenId(episodeId);
    setPlan(null);
    setPlanError("");
    try {
      setPlan(await getEpisodePlan(await freshToken(keycloak), episodeId));
    } catch (loadError) {
      setPlanError(loadError instanceof Error ? loadError.message : "Could not load the plan.");
    }
  }

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const records = await listEpisodes(await freshToken(keycloak));
        if (active) setEpisodes(records);
      } catch (loadError) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Could not load episodes.");
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [keycloak, reloadKey]);

  return (
    <section className="secondary-card approval-worklist">
      <div className="secondary-heading">
        <div>
          <span className="panel-eyebrow">EPISODE QUEUE</span>
          <h2>Episodes in the pipeline</h2>
        </div>
        <span className="quiet-count">{episodes.length} episodes</span>
      </div>
      {loading ? (
        <p role="status">Loading episodes from PostgreSQL...</p>
      ) : error ? (
        <Empty title="Episodes unavailable" detail={error} />
      ) : episodes.length === 0 ? (
        <Empty
          title="No episodes yet"
          detail="Episodes appear here once a topic is planned into an episode."
        />
      ) : (
        episodes.map((episode, index) => (
          <div key={episode.id}>
            <div className="approval-row">
              <span className={`approval-index approval-index-${(index % 3) + 1}`}>
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="approval-copy">
                <strong>{episode.title}</strong>
                <span>
                  {episode.topic_title}
                  {episode.latest_revision ? ` · revision ${episode.latest_revision}` : ""}
                  {` · ${episode.approvals} approval(s)`}
                  {episode.latest_render_status ? ` · render ${episode.latest_render_status}` : ""}
                </span>
              </span>
              <span className="approval-due">
                {episode.target_publish_date ?? "No publish date"}
              </span>
              <span className="status-pill status-neutral">{episode.status}</span>
              {episode.latest_revision && (
                <Button onClick={() => void togglePlan(episode.id)} type="button" variant="outline">
                  {openId === episode.id ? "Hide plan" : "View plan"}
                </Button>
              )}
            </div>
            {openId === episode.id &&
              (planError ? (
                <p role="alert">{planError}</p>
              ) : plan ? (
                <>
                  <PlanView record={plan} />
                  <ApprovalForm
                    episodeId={episode.id}
                    keycloak={keycloak}
                    onRecorded={() => setReloadKey((key) => key + 1)}
                    revision={plan.revision}
                  />
                </>
              ) : (
                <p role="status">Loading plan...</p>
              ))}
          </div>
        ))
      )}
      <p className="workspace-disclaimer">
        Plans are drafts for editor review. Approve a topic, then use Plan episode on its details
        panel. Open a plan to record an approval decision on its latest revision.
      </p>
    </section>
  );
}
