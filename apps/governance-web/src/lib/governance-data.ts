export type WorkspaceSection =
  | "overview"
  | "topics"
  | "research"
  | "fact-check"
  | "episodes"
  | "approvals"
  | "lineage"
  | "audit"
  | "reports";

export type ClaimStatus = "Needs review" | "Verified" | "Follow-up";
export type TopicStatus = "In review" | "Researching" | "Ready to plan" | "Approved";

export type EvidenceSource = {
  publisher: string;
  title: string;
  url: string;
  kind: "Primary source" | "Reference";
};

export type Topic = {
  id: string;
  title: string;
  subtitle: string;
  category: string;
  status: TopicStatus;
  claimStatus: ClaimStatus;
  sources: number;
  updated: string;
  owner: string;
  priority: "High" | "Medium" | "Low";
  claim: string;
  claimContext: string;
  evidence: EvidenceSource[];
};

export const navigationGroups: {
  title: string;
  items: { id: WorkspaceSection; label: string; icon: string; count?: number }[];
}[] = [
  {
    title: "Workspace",
    items: [
      { id: "overview", label: "Overview", icon: "layout-dashboard" },
      { id: "topics", label: "Topic queue", icon: "orbit", count: 12 },
      { id: "research", label: "Research desk", icon: "book-open-check", count: 4 },
      { id: "fact-check", label: "Fact verification", icon: "badge-check", count: 7 },
      { id: "episodes", label: "Episode queue", icon: "clapperboard", count: 3 },
    ],
  },
  {
    title: "Governance",
    items: [
      { id: "approvals", label: "Approval center", icon: "stamp", count: 3 },
      { id: "lineage", label: "Content lineage", icon: "git-branch" },
      { id: "audit", label: "Audit log", icon: "scroll-text" },
      { id: "reports", label: "Governance reports", icon: "chart-no-axes-combined" },
    ],
  },
];

export const initialTopics: Topic[] = [
  {
    id: "aditya-l1",
    title: "Our closest star, from a very special seat",
    subtitle: "How Aditya-L1 studies the Sun",
    category: "Solar missions",
    status: "In review",
    claimStatus: "Needs review",
    sources: 2,
    updated: "12 min ago",
    owner: "Ananya",
    priority: "High",
    claim: "Aditya-L1 is India's first space-based observatory dedicated to studying the Sun.",
    claimContext:
      "This is the lead learning claim for a short episode introducing India's solar mission.",
    evidence: [
      {
        publisher: "ISRO",
        title: "Aditya-L1 mission overview",
        url: "https://www.isro.gov.in/Aditya_L1.html",
        kind: "Primary source",
      },
      {
        publisher: "NASA Science",
        title: "Sun: facts and information",
        url: "https://science.nasa.gov/sun/facts/",
        kind: "Reference",
      },
    ],
  },
  {
    id: "chandrayaan-3",
    title: "A soft landing near the Moon's south pole",
    subtitle: "What Chandrayaan-3 taught us",
    category: "Lunar exploration",
    status: "Researching",
    claimStatus: "Follow-up",
    sources: 3,
    updated: "48 min ago",
    owner: "Rohan",
    priority: "High",
    claim: "Pragyan's observations help scientists study the lunar surface near the landing site.",
    claimContext:
      "Review the distinction between measurements made by the rover and broader conclusions.",
    evidence: [
      {
        publisher: "ISRO",
        title: "Chandrayaan-3 mission updates",
        url: "https://www.isro.gov.in/Chandrayaan3.html",
        kind: "Primary source",
      },
      {
        publisher: "NASA Science",
        title: "Moon: facts and exploration",
        url: "https://science.nasa.gov/moon/",
        kind: "Reference",
      },
    ],
  },
  {
    id: "compound-interest",
    title: "Why does compound interest grow over time?",
    subtitle: "A practical guide to compounding",
    category: "Personal finance",
    status: "Researching",
    claimStatus: "Needs review",
    sources: 0,
    updated: "Just now",
    owner: "Editorial team",
    priority: "Medium",
    claim: "A factual claim has not been drafted yet.",
    claimContext: "Research and evidence must be added before fact review.",
    evidence: [],
  },
  {
    id: "sourdough-rise",
    title: "What makes a sourdough starter rise?",
    subtitle: "A food-science question to research",
    category: "Food science",
    status: "Researching",
    claimStatus: "Needs review",
    sources: 0,
    updated: "Just now",
    owner: "Editorial team",
    priority: "Low",
    claim: "A factual claim has not been drafted yet.",
    claimContext: "Research and evidence must be added before fact review.",
    evidence: [],
  },
];

export const sectionDetails: Record<
  WorkspaceSection,
  { eyebrow: string; title: string; description: string }
> = {
  overview: {
    eyebrow: "Editorial workspace",
    title: "Good morning, team",
    description: "A clear view of what needs attention across your content pipeline.",
  },
  topics: {
    eyebrow: "Discovery",
    title: "Topic queue",
    description: "Shape ideas from any subject into research-ready video briefs.",
  },
  research: {
    eyebrow: "Knowledge desk",
    title: "Research desk",
    description: "Follow each topic from source discovery to an evidence-backed brief.",
  },
  "fact-check": {
    eyebrow: "Evidence review",
    title: "Fact verification",
    description: "Review each claim against traceable, independent evidence.",
  },
  episodes: {
    eyebrow: "Production planning",
    title: "Episode queue",
    description: "Track approved topics as they move toward scripts and storyboards.",
  },
  approvals: {
    eyebrow: "Editorial governance",
    title: "Approval center",
    description: "Make clear, revision-specific decisions before production moves ahead.",
  },
  lineage: {
    eyebrow: "Traceability",
    title: "Content lineage",
    description: "See how sources and reviewed claims connect to every episode.",
  },
  audit: {
    eyebrow: "Accountability",
    title: "Audit log",
    description: "A chronological record of review actions and content decisions.",
  },
  reports: {
    eyebrow: "Governance insights",
    title: "Governance reports",
    description: "A quick read on evidence coverage, review health, and release readiness.",
  },
};
