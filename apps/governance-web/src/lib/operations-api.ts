import { request } from "@/lib/topic-api";

export type GovernanceSummary = {
  topics_total: number;
  topics_approved: number;
  topics_with_multiple_sources: number;
  sources_linked: number;
  claims_total: number;
  claims_reviewed: number;
  claims_verified: number;
  episodes_total: number;
  episodes_with_approval: number;
  approvals_total: number;
};

export type ApprovalRecord = {
  id: string;
  episode_id: string;
  episode_title: string;
  revision: number;
  stage: string;
  decision: string;
  note: string | null;
  created_at: string;
};

export type AuditEventRecord = {
  id: string;
  object_type: string;
  object_id: string | null;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
};

export type RenderJobRecord = {
  id: string;
  episode_title: string;
  provider: string;
  remote_job_id: string | null;
  status: string;
  runtime_seconds: number | null;
  cost: string | null;
  failure_reason: string | null;
  created_at: string;
};

export type FailureRecord = {
  id: string;
  source: "workflow_step" | "render_job";
  title: string;
  detail: string;
  occurred_at: string;
};

export type MonitoringOverview = {
  window_hours: number;
  generated_at: string;
  workflow_runs: number;
  workflow_running: number;
  workflow_completed: number;
  workflow_failed: number;
  render_jobs: number;
  render_active: number;
  render_failed: number;
  spend: string;
  failed_spend: string;
  render_queue: Record<string, number>;
  series: { start: string; workflow_runs: number }[];
  recent_jobs: RenderJobRecord[];
  failures: FailureRecord[];
};

export function getGovernanceSummary(accessToken: string): Promise<GovernanceSummary> {
  return request<GovernanceSummary>("/governance/summary", accessToken);
}

export function listApprovals(accessToken: string): Promise<ApprovalRecord[]> {
  return request<ApprovalRecord[]>("/governance/approvals", accessToken);
}

export function listAuditEvents(accessToken: string): Promise<AuditEventRecord[]> {
  return request<AuditEventRecord[]>("/governance/audit-events", accessToken);
}

export function getMonitoringOverview(
  accessToken: string,
  hours: number,
): Promise<MonitoringOverview> {
  return request<MonitoringOverview>(`/monitoring/overview?hours=${hours}`, accessToken);
}

export type ClaimEvidenceRecord = {
  source_id: string;
  title: string;
  publisher: string;
  url: string;
  support_type: string;
  excerpt: string;
  independence_group: number;
};

export type ClaimRecord = {
  id: string;
  topic_id: string;
  topic_title: string;
  claim_text: string;
  category: string;
  verification_status: string;
  reviewed_at: string | null;
  review_note: string | null;
  evidence_count: number;
  supporting_source_count: number;
  independent_supporting_source_count: number;
  contradicting_source_count: number;
  has_conflicting_evidence: boolean;
  sources: ClaimEvidenceRecord[];
};

export type ReviewDecision = "verified" | "needs_follow_up" | "rejected";
export type ClaimCategory =
  | "verified"
  | "historical_record"
  | "scientific_theory"
  | "opinion"
  | "myth_or_legend"
  | "rejected";

export type EpisodeRecord = {
  id: string;
  topic_id: string;
  topic_title: string;
  title: string;
  status: string;
  target_publish_date: string | null;
  latest_revision: number | null;
  approvals: number;
  latest_render_status: string | null;
  created_at: string;
};

export function listClaims(accessToken: string): Promise<ClaimRecord[]> {
  return request<ClaimRecord[]>("/claims", accessToken);
}

export function reviewClaim(
  accessToken: string,
  claimId: string,
  input: { decision: ReviewDecision; category: ClaimCategory; note: string },
): Promise<ClaimRecord> {
  return request<ClaimRecord>(`/claims/${claimId}/review`, accessToken, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listEpisodes(accessToken: string): Promise<EpisodeRecord[]> {
  return request<EpisodeRecord[]>("/episodes", accessToken);
}

export type PlanStatement = {
  text: string;
  citations: { chunk_id: string; quote: string }[];
};

export type EpisodePlanRecord = {
  episode_id: string;
  revision: number;
  created_at: string;
  plan: {
    title: string;
    logline: string;
    target_duration_seconds: number;
    learning_objectives: string[];
    story_arc: string[];
    beats: {
      title: string;
      purpose: string;
      start_seconds: number;
      duration_seconds: number;
      narration: PlanStatement;
    }[];
    shorts: {
      title: string;
      hook: string;
      duration_seconds: number;
      narration: PlanStatement;
      call_to_action: string;
    }[];
    limitations: string[];
  };
};

export function generateEpisodePlan(
  accessToken: string,
  topicId: string,
): Promise<EpisodePlanRecord> {
  return request<EpisodePlanRecord>("/episodes/plan", accessToken, {
    method: "POST",
    body: JSON.stringify({ topic_id: topicId }),
  });
}

export type ResearchResult = {
  research_id: string;
  topic_id: string;
  revision: number;
  source_count: number;
  claim_count: number;
  open_questions: string[];
};

export function runResearch(accessToken: string, topicId: string): Promise<ResearchResult> {
  return request<ResearchResult>("/research", accessToken, {
    method: "POST",
    body: JSON.stringify({ topic_id: topicId }),
  });
}

export type ApprovalDecision = "approved" | "rejected" | "changes_requested";

export function recordPlanApproval(
  accessToken: string,
  episodeId: string,
  input: { decision: ApprovalDecision; note: string },
): Promise<ApprovalRecord> {
  return request<ApprovalRecord>(`/episodes/${episodeId}/approvals`, accessToken, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getEpisodePlan(
  accessToken: string,
  episodeId: string,
): Promise<EpisodePlanRecord> {
  return request<EpisodePlanRecord>(`/episodes/${episodeId}/plan`, accessToken);
}
