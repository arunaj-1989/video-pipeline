# Universal Video Maker Studio

Responsive editorial workspace built with Next.js App Router, TypeScript,
Tailwind CSS, Shadcn UI, and Lucide icons.

## Run locally

```powershell
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Preview-data behavior

The topic queue, claims, activity, approvals, lineage, and report values are
sample fixtures. Any topic and free-form category can be added, but changes
remain in the current browser session. The UI is not connected to the topic,
research, generation, or governance APIs and must not be used as a production
approval system.

## Governance surfaces

- Editorial overview and queue filters.
- Topic intake and topic detail.
- Research desk, claim verification, and linked evidence.
- Episode queue and approval-center preview.
- Content lineage, audit trail, and governance metrics.
- Monitoring preview for pipeline health, agent activity, GPU jobs,
  failures/retries, storage, rendering progress, and estimated spend.

The `/monitoring` dashboard is fixture-only. Its time-range selector and GPU
job filters affect local sample data; it does not connect to telemetry,
RunPod, billing, storage, or a monitoring API. Live refresh is unavailable,
and displayed health/spend values must not be interpreted as operational
readiness or actual costs.

## Validation

```powershell
npm run lint
npx tsc --noEmit
npm run build
```

## Security

Do not place API secrets in `NEXT_PUBLIC_*` variables. When API integration is
added, keep credentials server-side and enforce authorization in API use cases;
UI visibility is not an access-control boundary.
