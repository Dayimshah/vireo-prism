import { Database, GitBranch, ShieldCheck, Workflow } from 'lucide-react'

import { APP_NAME, ORG_NAME } from '@/api/config'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

/**
 * What this dashboard is, how it is built, and the legal footer every site needs.
 *
 * Deliberately outside {@link ROUTES}
 * ----------------------------------
 * The registry is the eleven analytics dashboards, and it carries a coverage invariant —
 * 54 GET routes = 50 page-owned + 4 non-page. An About page owns no endpoint, so adding it
 * there would either break that reconciliation or require a fake entry. It is wired as a
 * standalone route in `App.tsx` and linked from the sidebar footer instead, which keeps the
 * invariant honest and keeps About out of the analytics navigation where it does not belong.
 *
 * No data, no gate
 * ---------------
 * This page reads nothing from the API, so `AppLayout` lets it through without waiting on
 * `/meta/bounds`. That is the point: if the backend is cold or down, the one page that can
 * still explain what the reader is looking at should render.
 */
export function AboutPage() {
  return (
    <div className="max-w-3xl space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            {APP_NAME} — product analytics for {ORG_NAME}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-xs leading-relaxed text-muted-foreground">
          <p>
            {APP_NAME} is a full-stack analytics platform built over {ORG_NAME}, a fictional
            streaming service. It is a portfolio project: the data is synthetic, but the
            warehouse, the API, and this dashboard are real and run end to end.
          </p>
          <p>
            The data generator does not sprinkle random noise. It plants deliberate causal
            structure — a genre that retains better, a channel whose users churn, an
            experiment with a true effect — and the analytics layer has to rediscover those
            effects without being told they exist. Where the instruments can't recover a
            planted cause, that is written down rather than hidden. Knowing where a
            measurement breaks is the point of building it this way.
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <Feature
          icon={<Database className="size-4 text-primary" />}
          title="Warehouse"
          body="A 13-table PostgreSQL warehouse with 65 monthly partitions, materialized views, and tuned indexes over roughly 1.1M events. Dimensional model, built to be queried, not just stored."
        />
        <Feature
          icon={<Workflow className="size-4 text-primary" />}
          title="API"
          body="A 55-endpoint FastAPI with Redis caching, rate limiting, connection pooling, and RFC 7807 problem responses. The dashboard reads 50 of those endpoints; the rest are search, metadata, and health."
        />
        <Feature
          icon={<ShieldCheck className="size-4 text-primary" />}
          title="Tested on invariants"
          body="330 tests that assert properties — retention curves never rise, funnel steps only shrink — rather than pinning hardcoded values. They keep protecting the analytics when the seed data changes."
        />
        <Feature
          icon={<GitBranch className="size-4 text-primary" />}
          title="Deployed"
          body="PostgreSQL on Neon, API on Render, this dashboard on Vercel. Dockerized, with a Power BI star schema (~70 DAX measures) alongside the web app."
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">How I think about it</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-xs leading-relaxed text-muted-foreground">
          <p>
            <span className="font-medium text-foreground">Correctness before cleverness.</span>{' '}
            A number that is fast and wrong is worse than one that is slow and right. The tests
            and the reconciliation between the API docs and this navigation exist so the two
            cannot quietly disagree.
          </p>
          <p>
            <span className="font-medium text-foreground">Honest about limits.</span> Several
            endpoints reject a reporting window, the experiments page states its
            multiple-comparison caveat, and an empty database says so instead of rendering
            eleven broken panels. The failure modes are named where a reader meets them.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Site &amp; copyright</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-2xs leading-relaxed text-muted-foreground">
          <p>
            <span className="font-medium text-foreground">
              © {new Date().getFullYear()} Dayim Shah. All rights reserved.
            </span>{' '}
            The design and code of this project are my own work. {ORG_NAME} is a fictional
            company invented for this project; any resemblance to a real service is
            coincidental, and all figures shown are generated, not real user data.
          </p>
          <p>
            Source code is published on{' '}
            <a
              href="https://github.com/Dayimshah/vireo-prism"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline-offset-4 hover:underline"
            >
              GitHub
            </a>{' '}
            under the license stated in the repository. Product names and logos referenced in
            the stack — PostgreSQL, FastAPI, Redis, Power BI, Neon, Render, Vercel — are
            trademarks of their respective owners and are named only to describe the tools
            used.
          </p>
          <p>
            This site stores no personal data and sets no tracking cookies. It talks only to
            its own API to fetch the synthetic analytics you see.
          </p>
          <p className="pt-1">
            Built by Dayim Shah ·{' '}
            <a
              href="https://dayimshah.vercel.app"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline-offset-4 hover:underline"
            >
              Portfolio
            </a>{' '}
            ·{' '}
            <a
              href="https://www.linkedin.com/in/dayim-shah-612b03204"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline-offset-4 hover:underline"
            >
              LinkedIn
            </a>{' '}
            ·{' '}
            <a
              href="https://github.com/Dayimshah"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline-offset-4 hover:underline"
            >
              GitHub
            </a>
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

/** A single capability tile: icon, title, one paragraph. */
function Feature({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode
  title: string
  body: string
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          {icon}
          <CardTitle className="text-sm">{title}</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="text-xs leading-relaxed text-muted-foreground">{body}</CardContent>
    </Card>
  )
}
