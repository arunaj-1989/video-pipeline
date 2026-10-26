export type MetricRange = "24 hours" | "7 days" | "30 days";
export type JobStatus = "Running" | "Queued" | "Completed" | "Failed";

export type AgentRun = {
  name: string;
  responsibility: string;
  state: "Running" | "Idle" | "Needs attention";
  lastRun: string;
  duration: string;
  successRate: string;
  icon: string;
};

export type GpuJob = {
  id: string;
  episode: string;
  task: string;
  gpu: string;
  status: JobStatus;
  progress: number;
  elapsed: string;
  cost: string;
};

export const monitoringRanges: MetricRange[] = ["24 hours", "7 days", "30 days"];

export const overviewByRange: Record<
  MetricRange,
  { workflowRuns: string; successRate: string; gpuJobs: string; spend: string; chartValues: number[] }
> = {
  "24 hours": {
    workflowRuns: "18",
    successRate: "94.8%",
    gpuJobs: "06",
    spend: "₹3,482",
    chartValues: [18, 24, 19, 36, 29, 44, 33, 57, 42, 68, 51, 61],
  },
  "7 days": {
    workflowRuns: "126",
    successRate: "96.1%",
    gpuJobs: "32",
    spend: "₹18,760",
    chartValues: [24, 31, 27, 42, 35, 52, 45, 63, 49, 71, 58, 76],
  },
  "30 days": {
    workflowRuns: "514",
    successRate: "95.4%",
    gpuJobs: "118",
    spend: "₹68,940",
    chartValues: [29, 37, 33, 48, 39, 57, 48, 65, 53, 76, 62, 82],
  },
};

export const agents: AgentRun[] = [
  {
    name: "Topic discovery",
    responsibility: "Curated source scan",
    state: "Running",
    lastRun: "Started 2m ago",
    duration: "02:14",
    successRate: "98.2%",
    icon: "orbit",
  },
  {
    name: "Research agent",
    responsibility: "Evidence collection",
    state: "Running",
    lastRun: "Started 8m ago",
    duration: "08:32",
    successRate: "96.4%",
    icon: "search",
  },
  {
    name: "Fact check agent",
    responsibility: "Claim verification",
    state: "Needs attention",
    lastRun: "Retry queued · 4m ago",
    duration: "—",
    successRate: "91.7%",
    icon: "shield",
  },
  {
    name: "Episode planner",
    responsibility: "Outline and learning goals",
    state: "Idle",
    lastRun: "Completed 36m ago",
    duration: "01:48",
    successRate: "99.1%",
    icon: "plan",
  },
];

export const gpuJobs: GpuJob[] = [
  {
    id: "RP-2048",
    episode: "Compound interest · explainer",
    task: "Storyboard scene render",
    gpu: "RTX 4090",
    status: "Running",
    progress: 68,
    elapsed: "04:32",
    cost: "₹146",
  },
  {
    id: "RP-2047",
    episode: "Sourdough starter science",
    task: "Character motion pass",
    gpu: "RTX 6000 Ada",
    status: "Running",
    progress: 34,
    elapsed: "02:17",
    cost: "₹92",
  },
  {
    id: "RP-2046",
    episode: "Solar energy basics",
    task: "Style reference generation",
    gpu: "RTX 4090",
    status: "Queued",
    progress: 0,
    elapsed: "—",
    cost: "₹0",
  },
  {
    id: "RP-2045",
    episode: "How electric cars work",
    task: "Preview clip · scene 04",
    gpu: "A6000",
    status: "Failed",
    progress: 0,
    elapsed: "01:09",
    cost: "₹38",
  },
];

export const failureEvents = [
  {
    id: "ERR-0821",
    title: "Fact check provider timed out",
    detail: "Research workflow · Compound interest · retry scheduled",
    age: "4 min ago",
    severity: "warning" as const,
  },
  {
    id: "ERR-0819",
    title: "Render worker lost connection",
    detail: "GPU job RP-2045 · partial output retained",
    age: "11 min ago",
    severity: "error" as const,
  },
  {
    id: "ERR-0813",
    title: "Source fetch returned HTTP 429",
    detail: "Topic discovery · retry window 5 min",
    age: "26 min ago",
    severity: "warning" as const,
  },
];

export const storageVolumes = [
  { name: "Research snapshots", used: "42.8 GB", percentage: 61, color: "storage-saffron" },
  { name: "Generated assets", used: "108.2 GB", percentage: 76, color: "storage-teal" },
  { name: "Vector index", used: "8.4 GB", percentage: 28, color: "storage-blue" },
];
