import React, { useCallback, useEffect, useRef, useState } from "react";
import * as yaml from "js-yaml";
import { toast } from "sonner";
import {
  Blocks, Check, CheckCheck, CircleAlert, CircleCheckBig, CirclePause, CloudUpload,
  Copy, FileCode2, FilePen, FileSearch, Fingerprint, GitCompare, Info, PenLine,
  Play, Plus, Printer, RotateCcw, Save, ScanText, SearchCheck, Tags, TriangleAlert,
  Upload, X,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

// GET when no body/method; POST json when body given (pass {} for empty
// POST); pass an explicit method to PUT/DELETE (body optional for DELETE).
const api = async (path, body, method) => {
  const m = method || (body ? "POST" : "GET");
  const r = await fetch(path, m === "GET" ? undefined : {
    method: m,
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }
  return r.json();
};

const fmtTime = (iso) => (iso || "").slice(11, 19);
const fmtDate = (iso) => (iso || "").slice(0, 16).replace("T", " ");
const fmtCompact = (n) =>
  new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n || 0);

// Severity is always icon + label together, never color alone.
const SEV_META = {
  high: { Icon: CircleAlert, cls: "text-red-600", bg: "bg-red-50", label: "High priority" },
  medium: { Icon: TriangleAlert, cls: "text-amber-600", bg: "bg-amber-50", label: "Needs attention" },
  low: { Icon: Info, cls: "text-slate-500", bg: "bg-slate-100", label: "Minor note" },
};
const sevMeta = (s) =>
  SEV_META[s] || { Icon: SearchCheck, cls: "text-muted-foreground", bg: "bg-muted", label: s };

// ---------------------------------------------------------- plain-language helpers
// The backend deals in snake_case keys, rule codes and stage/decision pairs —
// accurate for engineers, opaque for anyone else. Everything below translates
// those into sentences without changing any data the API sends.

const KEY_LABELS = {
  monthly_commitment_minutes: "monthly minimum",
  auto_renewal_months: "auto-renewal term",
  per_minute_rate: "usage rate",
  payment_terms: "payment terms",
  billed_terms: "billed payment terms",
  billed_minutes: "billed minutes",
  billed_amount: "billed amount",
  billed_rate: "billed rate",
  invoice_period: "invoice period",
  sla_uptime: "SLA uptime",
};
const humanizeKey = (k) => KEY_LABELS[k] || (k || "").replace(/_/g, " ");
const KEY_RE = new RegExp(
  `\\b(${Object.keys(KEY_LABELS).sort((a, b) => b.length - a.length).join("|")})\\b`, "g"
);
const humanizeText = (t) => (t || "").replace(KEY_RE, (m) => KEY_LABELS[m]);

const RULE_TITLES = {
  "R1-auto-renewal-cap": "Auto-renewal runs longer than policy allows",
  "R2-payment-terms-cap": "Payment terms are looser than policy allows",
  "R3-sla-floor": "SLA guarantee is below the required floor",
  "R4-invoice-arithmetic": "Invoice math doesn't add up",
  "R5-minimum-commitment": "Billed under the monthly minimum",
  "R6-injection-quarantine": "Document tried to instruct the system",
  "R7-claims-cited": "A report claim has no evidence behind it",
  "classification-escalation": "Couldn't confidently classify a document",
  "unverifiable-extraction": "A claimed fact couldn't be confirmed in the source",
};
const ruleTitle = (id) => RULE_TITLES[id] || (id || "").replace(/^R\d+-/, "").replace(/-/g, " ");

// ------------------------------------------------------------- rule builder
// Lets a non-technical reviewer author playbook rules through a plain-English
// form instead of hand-writing YAML. The generated YAML (visible read-only
// below the builder) is exactly what gets saved — nothing is hidden, it's
// just never REQUIRED reading. Bounded to the checks app/rules_engine.py
// actually implements: adding a check type here without adding it there
// would let the builder promise something the engine can't enforce.
const KNOWN_FACT_KEYS = Object.keys(KEY_LABELS);
const CHECK_DEFS = {
  fact_max: {
    label: "Numeric ceiling", fields: ["key", "max"], defaultKey: "auto_renewal_months",
    hint: "A value must never go above a maximum.",
    describe: (r) => `${humanizeKey(r.key)} must not exceed ${r.max ?? "…"}.`,
  },
  fact_min: {
    label: "Numeric floor", fields: ["key", "min"], defaultKey: "sla_uptime",
    hint: "A value must never fall below a minimum.",
    describe: (r) => `${humanizeKey(r.key)} must be at least ${r.min ?? "…"}.`,
  },
  net_terms_max: {
    label: "Payment terms limit", fields: ["key", "max_days"], defaultKey: "payment_terms",
    hint: "Payment terms (e.g. net-30) must not be looser than a day count.",
    describe: (r) => `${humanizeKey(r.key)} must be net-${r.max_days ?? "…"} or stricter.`,
  },
  invoice_arithmetic: {
    label: "Invoice math check", fields: ["tolerance"],
    hint: "Invoice totals must equal billed minutes × the stated rate.",
    describe: (r) => `Invoice totals must match minutes × rate (tolerance ${r.tolerance ?? 0.01}).`,
  },
  minimum_commitment: {
    label: "Minimum billing commitment", fields: [],
    hint: "Billed minutes must meet the effective monthly minimum.",
    describe: () => "Billed minutes must meet the effective monthly commitment.",
  },
  claims_cited: {
    label: "Every report claim must cite evidence", fields: [],
    hint: "A report claim with no supporting fact is flagged.",
    describe: () => "Every report claim must cite at least one verified fact.",
  },
  injection_flag: {
    label: "Flag documents that try to instruct the system", fields: [],
    hint: "A source document containing instructions aimed at the system is reported, never obeyed.",
    describe: () => "Source documents must not attempt to instruct the analysis system.",
  },
};
const FIELD_LABELS = { key: "Applies to", max: "Maximum", min: "Minimum",
                       max_days: "Max days (net-__)", tolerance: "Tolerance ($)" };

const NEW_RULE_DEFAULTS = { check: "fact_max", key: "auto_renewal_months", max: "",
                            min: "", max_days: "", tolerance: "", severity: "high",
                            description: "" };

function slugId(text) {
  const base = (text || "rule").toLowerCase().trim()
    .replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "").slice(0, 40) || "rule";
  return `${base}-${Date.now().toString(36).slice(-4)}`;
}

// Flat {stages:[{name, rules:[...]}]} <-> a flat list of rules tagged with
// which stage they came from — the builder shows one flat list of cards;
// stage membership is preserved only so a Save round-trips it unchanged.
function playbookToRuleList(playbook) {
  if (!playbook || !Array.isArray(playbook.stages)) return [];
  return playbook.stages.flatMap((s) =>
    (s.rules || []).map((r) => ({ ...r, _stage: s.name })));
}
function ruleListToPlaybook(list) {
  const byStage = new Map();
  for (const r of list) {
    const { _stage, ...rule } = r;
    const name = _stage || "your-rules";
    if (!byStage.has(name)) byStage.set(name, []);
    byStage.get(name).push(rule);
  }
  return { stages: [...byStage.entries()].map(([name, rules]) => ({ name, rules })) };
}
function dumpYaml(playbook) {
  return yaml.dump(playbook, { noRefs: true, lineWidth: -1 });
}

const REASON_LABELS = {
  "full analysis": "a fresh analysis of every document",
  "update from new document(s)": "a newly arrived document",
  "overview counts refresh": "the summary counts changing",
};

const STATUS_LABELS = {
  running: "analyzing now", committing: "saving your decisions",
  awaiting_review: "waiting for your review", completed: "done", failed: "failed",
  ingested: "waiting to be analyzed", classified: "classified",
  quarantined: "needs your attention", extracted: "facts extracted",
};
const RUN_KIND_LABELS = { full: "Full analysis", update: "Update" };
const RUN_STATUS_SHORT = {
  running: "running", awaiting_review: "needs review",
  committing: "saving", completed: "done", failed: "failed",
};
// content_md always opens with its own "## Title" / "# Title" line; drop it
// where the title is already shown by the card around it, so it isn't printed twice.
const stripLeadingHeading = (text) => {
  const lines = (text || "").split("\n");
  if (/^#{1,2}\s/.test(lines[0] || "")) {
    lines.shift();
    while (lines[0] === "") lines.shift();
  }
  return lines.join("\n");
};
const STAGE_LABEL = {
  classify: "reading documents", extract: "pulling out facts",
  ground: "double-checking facts", reconcile: "comparing documents",
  compose: "drafting updates", examine: "checking your rules",
  propose: "preparing your review", commit: "saving decisions", render: "exporting",
};
const STAGE_ICONS = {
  classify: Tags, extract: ScanText, ground: CheckCheck,
  reconcile: GitCompare, compose: PenLine, examine: SearchCheck,
  propose: CirclePause, commit: Save, render: Printer,
};

function eventTone(decision) {
  if (/fail|drop|reject|quarantin/.test(decision)) return "bad";
  if (/retry|escalat|violat/.test(decision)) return "warn";
  return "ok";
}

// Turn one (stage, decision, detail) event into a sentence a reviewer can
// read without knowing the pipeline's internals. Falls back to null when a
// combination isn't covered — callers show the raw stage/decision instead.
function describeEvent(e) {
  const d = e.detail || {};
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  switch (`${e.stage}:${e.decision}`) {
    case "classify:classified":
      return `Classified "${d.doc}" as ${d.class}${d.entity ? ` (${d.entity})` : ""}.` +
        (d.injection_flagged ? " Flagged: contains instruction-like text." : "");
    case "classify:quarantined-escalated":
      return `Set "${d.doc}" aside — only ${pct(d.confidence)} confident in its classification.`;
    case "classify:entity-canonicalized":
      return `Matched "${d.model_said}" to the existing client "${d.canonical}".`;
    case "extract:extracted":
      return `Pulled ${d.candidate_facts} candidate fact(s) from "${d.doc}".`;
    case "extract:re-extracted":
      return `Tried again on "${d.doc}" — ${d.candidate_facts} candidate fact(s).`;
    case "ground:verified":
      return `Verified ${d.verified} fact(s) from "${d.doc}" against the source text` +
        (d.dropped ? `, dropped ${d.dropped} that couldn't be confirmed.` : ".");
    case "ground:retry-extraction":
      return `Couldn't confirm every fact in "${d.doc}" word-for-word — asking again (try ${d.attempt}).`;
    case "ground:unknown-keys-dropped":
      return `Dropped ${(d.keys || []).length} fact(s) from "${d.doc}" with an unrecognized label.`;
    case "reconcile:completed":
      return `Compared facts across documents: ${d.superseded} replaced by a newer document, ` +
        `${d.new_conflicts} new disagreement(s)` +
        (d.impacted_entities?.length ? ` — ${d.impacted_entities.join(", ")}.` : ".");
    case "compose:proposed":
      return `Drafted changes for ${(d.changed_sections || []).length} section(s)` +
        ((d.unchanged_sections || []).length
          ? `; ${d.unchanged_sections.length} left untouched, byte-for-byte.` : ".");
    case "propose:awaiting-human":
      return "Paused here — waiting on your review.";
    case "propose:nothing-to-review":
      return "Nothing needed your review this run.";
    case "propose:resumed":
    case "propose:resumed-all-items-decided":
      return "Every item has a decision — continuing.";
    case "commit:applied":
      return `Saved ${d.sections_applied} approved change(s)` +
        (d.sections_rejected ? `, discarded ${d.sections_rejected} rejected one(s).` : " to the report.");
    case "commit:section-rejected":
      return `Discarded the proposed change to "${d.section_key}"` +
        (d.feedback ? ` — note: "${d.feedback}"` : ".");
    case "render:exported":
      return "Exported a styled Word/PDF copy of the report.";
    case "render:skipped-render-disabled":
      return "Skipped the styled export for this run.";
    case "render:skipped-no-superdocs-key":
      return "Skipped the styled export — no export key configured; the report itself is up to date.";
    case "render:skipped-empty-register":
      return "Nothing to export yet — the report is still empty.";
    case "render:failed-nonfatal":
      return "The styled export failed, but the report itself is unaffected.";
    case "examine:completed":
      return `Checked ${d.rules_evaluated} playbook rule(s)` +
        (d.rules_source === "pile" ? " (your rules)" : " (system default)") +
        ` — ${d.new_findings} new issue(s).`;
    default:
      if (e.stage.startsWith("examine:")) {
        const group = e.stage.split(":")[1]?.replace(/-/g, " ");
        return e.decision === "clean" ? `${group}: nothing wrong.` : `${group}: found a problem.`;
      }
      return null;
  }
}

// Minimal renderer for the small, predictable markdown subset the register
// composer emits (#/## headings, pipe tables, a closing "---", paragraphs).
// Avoids pulling in a markdown dependency for content that never varies in
// shape, and never touches innerHTML. Element styling lives under .md-render
// in index.css.
function Markdown({ text }) {
  if (!text) return null;
  const lines = text.replace(/\n$/, "").split("\n");
  const cells = (row) => row.split("|").slice(1, -1).map((c) => c.trim());
  const isSep = (row) => cells(row).every((c) => /^:?-+:?$/.test(c));
  const blocks = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === "") continue;
    if (line.startsWith("## ")) { blocks.push(<h4 key={i}>{line.slice(3)}</h4>); continue; }
    if (line.startsWith("# ")) { blocks.push(<h3 key={i}>{line.slice(2)}</h3>); continue; }
    if (line.trim() === "---") { blocks.push(<hr key={i} />); continue; }
    if (line.startsWith("|")) {
      let j = i;
      const rows = [];
      while (j < lines.length && lines[j].startsWith("|")) { rows.push(lines[j]); j++; }
      const header = cells(rows[0]);
      const body = rows.slice(1).filter((r) => !isSep(r)).map(cells);
      blocks.push(body.length === 0
        ? <p className="text-muted-foreground" key={i}>Nothing extracted yet.</p>
        : (
          <table key={i}>
            <thead><tr>{header.map((h, k) => <th key={k}>{h}</th>)}</tr></thead>
            <tbody>{body.map((r, ri) => (
              <tr key={ri}>{r.map((c, ci) => <td key={ci}>{c}</td>)}</tr>
            ))}</tbody>
          </table>
        ));
      i = j - 1;
      continue;
    }
    blocks.push(<p key={i}>{line}</p>);
  }
  return <div className="md-render">{blocks}</div>;
}

// ------------------------------------------------------------ small pieces

const CHIP_TONES = {
  live: "bg-primary/10 text-primary",
  ok: "bg-emerald-100 text-emerald-700",
  warn: "bg-amber-100 text-amber-700",
  bad: "bg-red-100 text-red-700",
  idle: "bg-muted text-muted-foreground",
};

function StatusChip({ status }) {
  const tone = { running: "live", committing: "live", awaiting_review: "warn",
                 completed: "ok", failed: "bad" }[status] || "idle";
  const pulse = tone === "live" || tone === "warn";
  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-1 text-xs font-semibold",
      CHIP_TONES[tone])}>
      <i className={cn("size-2 rounded-full bg-current", pulse && "pulse-dot")} />
      {STATUS_LABELS[status] || status}
    </span>
  );
}

// Colored status word for dense table cells (chips would shout there).
const STATUS_TONE = {
  approved: "text-emerald-600", acknowledged: "text-emerald-600", analyzed: "text-emerald-600",
  rejected: "text-red-600", quarantined: "text-red-600",
  pending: "text-amber-600", open: "text-amber-600", new: "text-amber-600",
};
function StatusText({ status }) {
  return (
    <span className={cn("font-medium", STATUS_TONE[status] || "text-foreground/80")}>
      {STATUS_LABELS[status] || status}
    </span>
  );
}

const ITEM_BADGE_TONES = {
  approved: "bg-emerald-100 text-emerald-700",
  acknowledged: "bg-emerald-100 text-emerald-700",
  rejected: "bg-red-100 text-red-700",
  pending: "bg-amber-100 text-amber-700",
  open: "bg-amber-100 text-amber-700",
  new: "bg-amber-100 text-amber-700",
};
function ItemStatusBadge({ status }) {
  return (
    <Badge className={cn("ml-auto border-transparent capitalize",
      ITEM_BADGE_TONES[status] || "bg-secondary text-secondary-foreground")}>
      {status}
    </Badge>
  );
}

function IconTile({ Icon, cls, bg }) {
  return (
    <span className={cn("grid size-8 shrink-0 place-items-center rounded-lg", bg || "bg-muted")}>
      <Icon className={cn("size-4", cls)} />
    </span>
  );
}

function Disclosure({ summary, children, className }) {
  return (
    <details className={cn("mt-2", className)}>
      <summary className="cursor-pointer text-xs font-semibold text-primary select-none hover:underline">
        {summary}
      </summary>
      {children}
    </details>
  );
}

function Pre({ children, className }) {
  return (
    <pre className={cn(
      "my-2 overflow-x-auto rounded-lg border bg-muted/50 p-2.5 font-mono text-xs whitespace-pre-wrap",
      className)}>
      {children}
    </pre>
  );
}

function EmptyState({ Icon, title, sub, tone }) {
  return (
    <div className="my-3 flex flex-col items-center gap-2 rounded-xl border border-dashed bg-card/60 px-6 py-12 text-center">
      <span className={cn("grid size-12 place-items-center rounded-full",
        tone === "ok" ? "bg-emerald-100" : "bg-muted")}>
        <Icon className={cn("size-6", tone === "ok" ? "text-emerald-600" : "text-muted-foreground")} />
      </span>
      <b className="text-sm">{title}</b>
      {sub && <p className="max-w-md text-sm text-muted-foreground">{sub}</p>}
    </div>
  );
}

function StatTile({ label, value, sub, accent }) {
  return (
    <Card className="gap-1 px-4 py-4">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <span className={cn("text-2xl font-semibold tracking-tight", accent && "text-primary")}>
        {value}
      </span>
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
    </Card>
  );
}

// ---------------------------------------------------------------- pipeline map
const STEPS = [
  { label: "Documents", sub: "files in the pile" },
  { label: "Analyze", sub: "read · extract facts · compare" },
  { label: "Your review", sub: "approve or reject each item" },
  { label: "Save", sub: "only approved content lands" },
  { label: "Export", sub: "styled Word + PDF" },
];

function Stepper({ run, docsCount, pending }) {
  // active index: which step the selected run is on right now
  let active = docsCount > 0 ? 1 : 0;
  if (run) {
    active = { running: 1, awaiting_review: 2, committing: 3,
               completed: 5, failed: 1 }[run.status] ?? 1;
  }
  const lastEvent = run && run.events.length
    ? run.events[run.events.length - 1] : null;
  return (
    <div className="mt-4 mb-1 flex flex-wrap">
      {STEPS.map((s, i) => {
        const state = i < active ? "done" : i === active ? "active" : "todo";
        let sub = s.sub;
        if (i === 1 && state === "active" && lastEvent) {
          const stage = lastEvent.stage.split(":")[0];
          sub = `right now: ${STAGE_LABEL[stage] || stage}`;
        }
        if (i === 2 && state === "active") sub = `${pending} item(s) waiting for you`;
        return (
          <div className="flex min-w-[45%] flex-1 items-center py-1 sm:min-w-[150px]" key={s.label}>
            <div className={cn(
              "grid size-8 shrink-0 place-items-center rounded-full border-2 text-[13px] font-bold",
              state === "done" && "border-emerald-600 bg-emerald-600 text-white",
              state === "active" && "step-ring border-primary bg-card text-primary",
              state === "todo" && "border-transparent bg-secondary text-muted-foreground")}>
              {state === "done" ? <Check className="size-4" /> : i + 1}
            </div>
            <div className="ml-2.5 min-w-0 leading-tight">
              <b className={cn("block text-[13px]", state === "active" && "text-primary")}>
                {s.label}
              </b>
              <span className="block max-w-[165px] truncate text-[11px] text-muted-foreground">
                {sub}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div className={cn("mx-2.5 hidden h-0.5 flex-1 sm:block",
                state === "done" ? "bg-emerald-200" : "bg-border")} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- review card
function itemHeadline(item) {
  const p = item.payload;
  if (item.item_type === "section_update") {
    return { Icon: FilePen, cls: "text-primary", bg: "bg-primary/10",
             title: `Update: ${p.title}`,
             sub: `Why: ${REASON_LABELS[p.reason] || p.reason || "content changed"}` };
  }
  if (item.item_type === "conflict") {
    return { Icon: TriangleAlert, cls: "text-amber-600", bg: "bg-amber-50",
             title: `Disagreement: ${p.entity}`, sub: humanizeKey(p.key) };
  }
  const sev = sevMeta(p.severity);
  return { Icon: sev.Icon, cls: sev.cls, bg: sev.bg, title: ruleTitle(p.rule_id),
           sub: `${sev.label}${p.entity ? ` · ${p.entity}` : ""}` };
}

const CARD_EDGE = {
  pending: "border-l-4 border-l-amber-400",
  approved: "border-l-4 border-l-emerald-500",
  rejected: "border-l-4 border-l-red-400 opacity-70",
};

function ItemCard({ item, onDecide }) {
  const [feedback, setFeedback] = useState("");
  const p = item.payload;
  const decided = item.status !== "pending";
  const h = itemHeadline(item);
  return (
    <Card className={cn("my-2.5 gap-2", CARD_EDGE[item.status])}>
      <CardHeader className="flex-nowrap items-start">
        <IconTile Icon={h.Icon} cls={h.cls} bg={h.bg} />
        <div className="min-w-0 leading-snug">
          <b className="block text-[14.5px]">{h.title}</b>
          <span className="text-xs text-muted-foreground">{h.sub}</span>
        </div>
        <ItemStatusBadge status={item.status} />
      </CardHeader>
      <CardContent>
        {item.item_type === "section_update" && (
          <>
            <Markdown text={stripLeadingHeading(p.content_md)} />
            <Disclosure summary="View raw text (for exact diffing)">
              <Pre>{p.content_md}</Pre>
            </Disclosure>
          </>
        )}
        {item.item_type === "conflict" && <p className="text-sm">{humanizeText(p.detail)}</p>}
        {item.item_type === "finding" && (
          <>
            <p className="text-sm">{humanizeText(p.message)}</p>
            {p.quote && (
              <blockquote className="my-2 rounded-r-lg border-l-2 bg-muted/50 px-3 py-1.5 text-sm text-muted-foreground italic">
                “{p.quote}”
              </blockquote>
            )}
            <p className="mt-1.5 font-mono text-[11px] text-muted-foreground">rule {p.rule_id}</p>
          </>
        )}

        {!decided && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Input className="h-8 min-w-[180px] flex-1" placeholder="Add a note (optional)"
                   value={feedback} onChange={(e) => setFeedback(e.target.value)} />
            <Button size="sm" variant="success"
                    onClick={() => onDecide(item.id, true, feedback)}>
              <Check /> Approve
            </Button>
            <Button size="sm" variant="destructive"
                    onClick={() => onDecide(item.id, false, feedback)}>
              <X /> Reject
            </Button>
          </div>
        )}
        {decided && item.feedback && (
          <p className="mt-2 text-sm text-muted-foreground">Note: {item.feedback}</p>
        )}
      </CardContent>
    </Card>
  );
}

function DraftRuleCard({ rule, onDelete }) {
  const def = CHECK_DEFS[rule.check];
  const sev = sevMeta(rule.severity);
  return (
    <Card className="gap-1.5 py-3">
      <CardHeader className="flex-nowrap items-start">
        <IconTile Icon={sev.Icon} cls={sev.cls} bg={sev.bg} />
        <div className="min-w-0 leading-snug">
          <b className="block text-sm">{RULE_TITLES[rule.id] || rule.description}</b>
          <span className="text-xs text-muted-foreground">
            {sev.label} · {rule._stage}
          </span>
        </div>
        <Button size="icon-sm" variant="ghost" className="ml-auto"
                title="Remove this rule"
                onClick={() => onDelete(rule._stage, rule.id)}>
          <X />
        </Button>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          {def ? def.describe(rule) : `unrecognized check: ${rule.check}`}
        </p>
      </CardContent>
    </Card>
  );
}

const EVENT_TONES = {
  bad: { tile: "bg-red-50", icon: "text-red-600", text: "text-red-700" },
  warn: { tile: "bg-amber-50", icon: "text-amber-600", text: "text-amber-700" },
  ok: { tile: "bg-muted", icon: "text-muted-foreground", text: "" },
};

function Activity({ events }) {
  return (
    <Card className="my-2 gap-0 py-1">
      {events.map((e, i) => {
        const plain = describeEvent(e);
        const tone = EVENT_TONES[eventTone(e.decision)];
        const stage = e.stage.split(":")[0];
        const StageIcon = STAGE_ICONS[stage] || SearchCheck;
        return (
          <div className={cn("flex gap-3 px-4 py-2.5", i > 0 && "border-t")} key={i}>
            <span className={cn("mt-0.5 grid size-7 shrink-0 place-items-center rounded-md", tone.tile)}>
              <StageIcon className={cn("size-3.5", tone.icon)} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-3">
                <b className={cn("text-[13.5px] font-semibold", tone.text)}>
                  {plain || `${e.stage} — ${e.decision}`}
                </b>
                <span className="font-mono text-xs text-muted-foreground">{fmtTime(e.ts)}</span>
              </div>
              {!plain && <span className="text-xs text-muted-foreground">stage: {e.stage}</span>}
              <details>
                <summary className="cursor-pointer text-xs text-muted-foreground select-none hover:text-primary">
                  technical detail
                </summary>
                <Pre className="text-[11px]">{JSON.stringify(e.detail, null, 2)}</Pre>
              </details>
            </div>
          </div>
        );
      })}
    </Card>
  );
}

const TABS = ["documents", "rules", "review", "timeline", "register", "findings",
              "provenance", "costs"];
const TAB_LABELS = {
  documents: "Documents", rules: "Rules", review: "Review", timeline: "Activity",
  register: "Report", findings: "Issues", provenance: "History", costs: "Cost",
};
const TAB_CAPTIONS = {
  documents: "Every source file the analyst has read so far.",
  rules: "Hand this pile the rules it should be examined against — your own playbook, not a shared default.",
  review: "Approve or reject each proposed change — nothing is final until you say so.",
  timeline: "A play-by-play of what the analyst just did, in order.",
  register: "The living report. Every figure links back to the exact document it came from.",
  findings: "Anything that breaks a rule in your playbook — even a clean pass is reported.",
  provenance: "What changed, when, and which document caused it.",
  costs: "What this analysis cost, in time and money.",
};

// ---------------------------------------------------------------------- app
export default function App() {
  const [piles, setPiles] = useState(null);      // null = not loaded yet
  const [pileId, setPileId] = useState(null);
  const [docs, setDocs] = useState([]);
  const [runs, setRuns] = useState([]);
  const [runSel, setRunSel] = useState({ id: null, manual: false });
  const [items, setItems] = useState([]);
  const [register, setRegister] = useState(null);
  const [audit, setAudit] = useState(null);
  const [findings, setFindings] = useState([]);
  const [costs, setCosts] = useState(null);
  const [rules, setRules] = useState(null);
  const [rulesDraft, setRulesDraft] = useState("");           // the YAML text that Save submits
  const [rulesDraftObj, setRulesDraftObj] = useState(null);   // { stages: [...] } that backs the builder
  const [rulesParseError, setRulesParseError] = useState(""); // set when raw-YAML edits can't be shown as cards
  const [rulesMode, setRulesMode] = useState("builder");      // "builder" | "yaml"
  const [rulesDirty, setRulesDirty] = useState(false);
  const [rulesBusy, setRulesBusy] = useState(false);
  const [newRule, setNewRule] = useState(NEW_RULE_DEFAULTS);
  const [health, setHealth] = useState(null);
  const [sampleSets, setSampleSets] = useState([]);
  const [tab, setTab] = useState(() => {
    const h = window.location.hash.slice(1);
    return TABS.includes(h) ? h : "documents";
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [newPile, setNewPile] = useState("");
  const [creating, setCreating] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const prevStatus = useRef({});
  const fileInput = useRef(null);

  const say = (msg) => toast(msg);

  const refresh = useCallback(async () => {
    try {
      const [ps, h] = await Promise.all([api("/piles"), api("/health")]);
      setPiles(ps);
      setHealth(h);
      if (!sampleSets.length) setSampleSets(await api("/corpus"));
      const pid = pileId && ps.some((p) => p.id === pileId)
        ? pileId : (ps.length ? ps[ps.length - 1].id : null);
      if (!pid) return;
      if (pid !== pileId) setPileId(pid);
      const [ds, rs] = await Promise.all([
        api(`/piles/${pid}/documents`), api(`/piles/${pid}/runs`),
      ]);
      setDocs(ds);
      setRuns(rs);
      const newest = rs.length ? rs[rs.length - 1].id : null;
      const rid = runSel.manual && rs.some((r) => r.id === runSel.id)
        ? runSel.id : newest;
      if (rid !== runSel.id) setRunSel({ id: rid, manual: runSel.manual });
      if (rid) {
        setItems(await api(`/runs/${rid}/pending?status=`));
        const r = rs.find((x) => x.id === rid);
        const prev = prevStatus.current[rid];
        if (prev === "running" && r.status === "awaiting_review") {
          setTab("review");
          say("The analyst paused — items are waiting for your review.");
        }
        if ((prev === "committing" || prev === "awaiting_review")
            && r.status === "completed") {
          say("Run complete — the report is up to date.");
        }
        prevStatus.current[rid] = r.status;
      } else {
        setItems([]);
      }
      const [reg, aud, fnd, cst, rul] = await Promise.all([
        api(`/piles/${pid}/register`), api(`/piles/${pid}/audit`),
        api(`/piles/${pid}/findings`), api(`/piles/${pid}/costs`),
        api(`/piles/${pid}/rules`),
      ]);
      setRegister(reg); setAudit(aud); setFindings(fnd); setCosts(cst);
      setRules(rul);
      if (!rulesDirty) {
        setRulesDraft(rul.rules_yaml);
        try {
          setRulesDraftObj(yaml.load(rul.rules_yaml));
          setRulesParseError("");
        } catch (e) { setRulesParseError(String(e.message || e)); }
      }
      setError("");
    } catch (e) {
      setError(String(e.message || e));
    }
  }, [pileId, runSel, sampleSets.length, rulesDirty]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2500);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    window.history.replaceState(null, "", "#" + tab);
  }, [tab]);

  // ------------------------------------------------------------- actions
  const createPile = async () => {
    const name = newPile.trim();
    if (!name) return;
    try {
      const p = await api("/piles", { name });
      setPileId(p.id);
      setRunSel({ id: null, manual: false });
      setRulesDirty(false);
      setNewPile(""); setCreating(false);
      setTab("documents");
      say(`Pile "${p.name}" ready — add documents next.`);
      refresh();
    } catch (e) { setError(String(e.message || e)); }
  };

  const uploadFiles = async (fileList) => {
    if (!pileId || !fileList.length) return;
    setBusy(true);
    let ok = 0, dup = 0;
    const fails = [];
    for (const f of fileList) {
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await fetch(`/piles/${pileId}/documents`, { method: "POST", body: fd });
        if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
        (await r.json()).duplicate ? dup++ : ok++;
      } catch (e) { fails.push(`${f.name}: ${e.message}`); }
    }
    setBusy(false);
    if (fails.length) setError(`Some uploads were refused — ${fails.join("; ")}`);
    say(`${ok} document(s) added${dup ? `, ${dup} duplicate(s) skipped` : ""}.`);
    refresh();
  };

  const loadSamples = async (setName) => {
    setBusy(true);
    try {
      const added = await api(`/piles/${pileId}/documents/sample`, { set: setName });
      say(`${added.length} sample document(s) loaded — now click ▶ Run analysis.`);
      setTab("documents");
      refresh();
    } catch (e) { setError(String(e.message || e)); }
    setBusy(false);
  };

  const startRun = async () => {
    setBusy(true);
    try {
      const r = await api(`/piles/${pileId}/runs`, { kind: "full" });
      setRunSel({ id: r.id, manual: false });
      setTab("timeline");
      say("Analysis started — it will pause for your review.");
      refresh();
    } catch (e) { setError(String(e.message || e)); }
    setBusy(false);
  };

  const decide = async (id, approve, feedback) => {
    try {
      await api(`/items/${id}/decision`,
        { approve, decided_by: "review-ui", feedback: feedback || null });
      refresh();
    } catch (e) { setError(String(e.message || e)); }
  };

  const resume = async () => {
    try {
      await api(`/runs/${runSel.id}/resume?wait=false`, {});
      say("Resuming — approved items are being saved.");
      setTab("timeline");
      refresh();
    } catch (e) { setError(String(e.message || e)); }
  };

  const saveRules = async () => {
    setRulesBusy(true);
    try {
      const r = await api(`/piles/${pileId}/rules`, { rules_yaml: rulesDraft }, "PUT");
      setRulesDirty(false);
      say(`Rules saved — ${r.rule_count} rule(s) across ${r.stages.length} stage(s). The next run uses them.`);
      refresh();
    } catch (e) { setError(String(e.message || e)); }
    setRulesBusy(false);
  };

  const resetRules = async () => {
    setRulesBusy(true);
    try {
      await api(`/piles/${pileId}/rules`, undefined, "DELETE");
      setRulesDirty(false);
      say("Reverted to the system default playbook.");
      refresh();
    } catch (e) { setError(String(e.message || e)); }
    setRulesBusy(false);
  };

  // ---------------------------------------------------------- rule builder
  const applyDraftObj = (next) => {
    setRulesDraftObj(next);
    setRulesDraft(dumpYaml(next));
    setRulesDirty(true);
  };

  const addRule = () => {
    const def = CHECK_DEFS[newRule.check];
    const rule = { id: slugId(newRule.description), severity: newRule.severity,
                   description: newRule.description.trim() || def.describe(newRule),
                   check: newRule.check };
    for (const f of def.fields) {
      const raw = newRule[f];
      if (f === "tolerance" && !raw) continue;   // optional — the engine defaults it
      rule[f] = (f === "key") ? raw : Number(raw);
    }
    const list = [...playbookToRuleList(rulesDraftObj), { ...rule, _stage: "your-rules" }];
    applyDraftObj(ruleListToPlaybook(list));
    setNewRule({ ...NEW_RULE_DEFAULTS });
    say("Rule added — click Save to make it active.");
  };

  const deleteRule = (stageName, ruleId) => {
    const list = playbookToRuleList(rulesDraftObj)
      .filter((r) => !(r._stage === stageName && r.id === ruleId));
    applyDraftObj(ruleListToPlaybook(list));
  };

  const ruleFieldReady = () => {
    const def = CHECK_DEFS[newRule.check];
    return def.fields.every((f) => f === "tolerance" || String(newRule[f] ?? "").trim() !== "");
  };

  const switchToYamlMode = () => setRulesMode("yaml");
  const switchToBuilderMode = () => {
    try {
      const parsed = yaml.load(rulesDraft);
      setRulesDraftObj(parsed);
      setRulesParseError("");
      setRulesMode("builder");
    } catch (e) {
      setRulesParseError(String(e.message || e));   // stay in YAML mode until it's fixable
    }
  };

  const copyRegister = async () => {
    try {
      await navigator.clipboard.writeText(register.markdown);
      say("Report copied to clipboard as markdown.");
    } catch { setError("Clipboard unavailable in this browser context."); }
  };

  // ------------------------------------------------------------- derived
  const pile = (piles || []).find((p) => p.id === pileId);
  const run = runs.find((r) => r.id === runSel.id);
  const pending = items.filter((i) => i.status === "pending").length;
  const activeRun = runs.find((r) =>
    ["running", "awaiting_review", "committing"].includes(r.status));

  const onDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    uploadFiles([...e.dataTransfer.files]);
  };

  // ------------------------------------------------------------- render
  if (piles && piles.length === 0) {
    return (
      <div className="mx-auto max-w-[1180px] px-4 pb-16">
        <Header health={health} />
        <Card className="mx-auto mt-16 max-w-[560px] gap-2 px-7 py-7">
          <h2 className="text-lg font-semibold">Welcome — create your first pile</h2>
          <p className="text-sm text-muted-foreground">
            A <b className="text-foreground">pile</b> is an isolated set of documents
            (contracts, amendments, invoices) that the analyst turns into a living
            obligations register.
          </p>
          <div className="mt-3 flex gap-2">
            <Input autoFocus placeholder="pile name, e.g. meridian-clients"
                   value={newPile} onChange={(e) => setNewPile(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && createPile()} />
            <Button onClick={createPile}>Create pile</Button>
          </div>
        </Card>
        {error && <ErrorBanner error={error} onDismiss={() => setError("")} />}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1180px] px-4 pb-16">
      <Header health={health} />

      {/* ------------------------------------------------ control bar */}
      <Card className="flex-row flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold tracking-wider text-muted-foreground uppercase"
                title="A pile is an isolated set of related documents.">
            Pile
          </span>
          {creating ? (
            <span className="flex items-center gap-1.5">
              <Input autoFocus className="h-8 w-44" placeholder="new pile name" value={newPile}
                     onChange={(e) => setNewPile(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && createPile()} />
              <Button size="sm" onClick={createPile}>Create</Button>
              <Button size="icon-sm" variant="ghost" onClick={() => setCreating(false)}>
                <X />
              </Button>
            </span>
          ) : (
            <>
              <Select value={pileId ?? undefined}
                      onValueChange={(v) => {
                        setPileId(v);
                        setRunSel({ id: null, manual: false });
                        setRulesDirty(false);
                      }}>
                <SelectTrigger size="sm" className="w-44">
                  <SelectValue placeholder="select a pile" />
                </SelectTrigger>
                <SelectContent>
                  {(piles || []).map((p) =>
                    <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <Button size="sm" variant="outline" title="Create a new pile"
                      onClick={() => setCreating(true)}>
                <Plus /> New pile
              </Button>
            </>
          )}
        </div>

        <div className="flex flex-1 items-center justify-center gap-2">
          <Button size="sm" variant="outline" disabled={busy}
                  onClick={() => fileInput.current.click()}>
            <Upload /> Upload documents
          </Button>
          <input ref={fileInput} type="file" multiple hidden
                 accept=".md,.txt,.html,.docx,.pdf"
                 onChange={(e) => { uploadFiles([...e.target.files]); e.target.value = ""; }} />
          <Button size="sm" disabled={busy || !docs.length || !!activeRun}
                  title={!docs.length ? "Add documents first"
                        : activeRun ? "A run is already in progress" : ""}
                  onClick={startRun}>
            <Play /> Run analysis
          </Button>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold tracking-wider text-muted-foreground uppercase">
            Run
          </span>
          {runs.length ? (
            <Select value={runSel.id ?? undefined}
                    onValueChange={(v) => setRunSel({ id: v, manual: true })}>
              <SelectTrigger size="sm" className="max-w-95">
                <SelectValue placeholder="select a run" />
              </SelectTrigger>
              <SelectContent>
                {[...runs].reverse().map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    {RUN_KIND_LABELS[r.kind] || r.kind} · {fmtDate(r.started_at)} · {RUN_STATUS_SHORT[r.status] || r.status}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Select disabled>
              <SelectTrigger size="sm">
                <SelectValue placeholder="no runs yet" />
              </SelectTrigger>
            </Select>
          )}
          {run && <StatusChip status={run.status} />}
        </div>
      </Card>

      <Stepper run={run} docsCount={docs.length} pending={pending} />

      {error && <ErrorBanner error={error} onDismiss={() => setError("")} />}
      {run && run.status === "failed" && (
        <Alert variant="destructive" className="my-2">
          <CircleAlert />
          <AlertTitle>Run failed</AlertTitle>
          <AlertDescription>{run.error}</AlertDescription>
        </Alert>
      )}

      <Tabs value={tab} onValueChange={setTab} className="mt-4">
        <TabsList className="h-auto w-full justify-start overflow-x-auto">
          {TABS.map((t) => (
            <TabsTrigger key={t} value={t} className="gap-1.5 px-3 py-1.5">
              {TAB_LABELS[t]}
              {t === "documents" && (
                <Badge variant="secondary" className="h-4 min-w-4 px-1 text-[10px]">
                  {docs.length}
                </Badge>
              )}
              {t === "review" && pending > 0 && (
                <Badge className="pulse-dot h-4 min-w-4 border-transparent bg-amber-500 px-1 text-[10px] text-white">
                  {pending}
                </Badge>
              )}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <p className="mt-2 mb-2 text-sm text-muted-foreground">{TAB_CAPTIONS[tab]}</p>

      {/* ------------------------------------------------ documents tab */}
      {tab === "documents" && (
        <section>
          <div className={cn(
                 "my-3 cursor-pointer rounded-xl border-2 border-dashed bg-card p-7 text-center transition-colors",
                 dragOver ? "border-primary bg-accent"
                          : "border-input hover:border-primary/60 hover:bg-accent/50")}
               onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
               onDragLeave={() => setDragOver(false)}
               onDrop={onDrop}
               onClick={() => fileInput.current.click()}>
            <CloudUpload className="mx-auto mb-2 size-8 text-muted-foreground" />
            <b>Drop files here</b> or click to browse
            <div className="mt-1 text-sm text-muted-foreground">md · txt · html · docx · pdf</div>
          </div>
          {docs.length === 0 && sampleSets.length > 0 && (
            <Card className="gap-1.5">
              <CardHeader><CardTitleText>…or try the bundled sample corpus</CardTitleText></CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  Fictional voice-AI vendor "Meridian Voice Systems": client
                  contracts, amendments and invoices with planted conflicts,
                  a compliance breach and a prompt-injection attempt.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {sampleSets.map((s) => (
                    <Button key={s.name} size="sm" disabled={busy}
                            variant={s.name === "seed" ? "default" : "outline"}
                            onClick={() => loadSamples(s.name)}>
                      Load "{s.name}" ({s.files.length} files)
                    </Button>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
          {docs.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>file</TableHead><TableHead>format</TableHead>
                  <TableHead>class</TableHead><TableHead>client</TableHead>
                  <TableHead>status</TableHead><TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {docs.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="font-medium">{d.filename}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="px-1.5 font-mono text-[10px] uppercase">
                        {d.format}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {d.doc_class || <span className="text-muted-foreground">pending analysis</span>}
                    </TableCell>
                    <TableCell>{d.entity || ""}</TableCell>
                    <TableCell><StatusText status={d.status} /></TableCell>
                    <TableCell>
                      {d.injection_flagged && (
                        <Badge variant="destructive"><TriangleAlert /> injection flagged</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {pile && (
            <p className="mt-4 text-sm text-muted-foreground">
              Watched folder: files dropped into{" "}
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                corpus/incoming/{pile.name}/
              </code>{" "}
              are ingested automatically as focused update runs — no clicks needed.
            </p>
          )}
        </section>
      )}

      {/* ------------------------------------------------ rules tab */}
      {tab === "rules" && (
        <section>
          {rules && (
            <Card className="gap-2">
              <CardHeader>
                <CardTitleText>Active playbook</CardTitleText>
                <span className={cn(
                  "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold",
                  rules.source === "pile" ? CHIP_TONES.live : CHIP_TONES.idle)}>
                  <i className="size-2 rounded-full bg-current" />
                  {rules.source === "pile" ? "your rules" : "system default"}
                </span>
                <span className="text-sm text-muted-foreground">
                  {rules.rule_count} rule(s) across {rules.stages.length} stage(s)
                </span>
                <span className="ml-auto inline-flex items-center gap-0.5 rounded-lg bg-muted p-[3px]">
                  <button className={segCls(rulesMode === "builder")} onClick={switchToBuilderMode}>
                    <Blocks className="size-3.5" /> Builder
                  </button>
                  <button className={segCls(rulesMode === "yaml")} onClick={switchToYamlMode}>
                    <FileCode2 className="size-3.5" /> Raw YAML
                  </button>
                </span>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  Hand this pile a compliance checklist, a contract playbook,
                  or a style guide — as rules — and every run against it is
                  examined against YOUR rules, not a shared default. Build
                  rules below with plain-English fields; the YAML underneath
                  (always visible, never required reading) is exactly what
                  gets saved. Each rule names one of the checks the engine
                  actually implements — an unknown check is refused with the
                  specific reason, not silently ignored.
                </p>

                {rulesMode === "builder" ? (
                  <>
                    <div className="my-3 flex flex-col gap-2">
                      {playbookToRuleList(rulesDraftObj).map((r) => (
                        <DraftRuleCard key={`${r._stage}:${r.id}`} rule={r} onDelete={deleteRule} />
                      ))}
                      {playbookToRuleList(rulesDraftObj).length === 0 && (
                        <p className="text-sm text-muted-foreground">No rules yet — add one below.</p>
                      )}
                    </div>

                    <Card className="my-3 gap-2 bg-muted/40 shadow-none">
                      <CardHeader><CardTitleText>Add a rule</CardTitleText></CardHeader>
                      <CardContent>
                        <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3">
                          <label className="flex flex-col gap-1 text-xs">
                            <span className="text-muted-foreground">Check type</span>
                            <Select value={newRule.check}
                                    onValueChange={(check) => {
                                      setNewRule({ ...NEW_RULE_DEFAULTS, check,
                                        key: CHECK_DEFS[check].defaultKey || "" });
                                    }}>
                              <SelectTrigger size="sm" className="w-full">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {Object.entries(CHECK_DEFS).map(([k, d]) =>
                                  <SelectItem key={k} value={k}>{d.label}</SelectItem>)}
                              </SelectContent>
                            </Select>
                          </label>
                          {CHECK_DEFS[newRule.check].fields.includes("key") && (
                            <label className="flex flex-col gap-1 text-xs">
                              <span className="text-muted-foreground">{FIELD_LABELS.key}</span>
                              <Select value={newRule.key}
                                      onValueChange={(k) => setNewRule({ ...newRule, key: k })}>
                                <SelectTrigger size="sm" className="w-full">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {KNOWN_FACT_KEYS.map((k) =>
                                    <SelectItem key={k} value={k}>{humanizeKey(k)}</SelectItem>)}
                                </SelectContent>
                              </Select>
                            </label>
                          )}
                          {["max", "min", "max_days", "tolerance"]
                            .filter((f) => CHECK_DEFS[newRule.check].fields.includes(f))
                            .map((f) => (
                              <label key={f} className="flex flex-col gap-1 text-xs">
                                <span className="text-muted-foreground">{FIELD_LABELS[f]}</span>
                                <Input type="number" className="h-8"
                                       step={f === "tolerance" ? "0.01" : "1"}
                                       placeholder={f === "tolerance" ? "0.01 (optional)" : ""}
                                       value={newRule[f]}
                                       onChange={(e) => setNewRule({ ...newRule, [f]: e.target.value })} />
                              </label>
                            ))}
                          <label className="flex flex-col gap-1 text-xs">
                            <span className="text-muted-foreground">Severity</span>
                            <Select value={newRule.severity}
                                    onValueChange={(s) => setNewRule({ ...newRule, severity: s })}>
                              <SelectTrigger size="sm" className="w-full">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="high">High</SelectItem>
                                <SelectItem value="medium">Medium</SelectItem>
                                <SelectItem value="low">Low</SelectItem>
                              </SelectContent>
                            </Select>
                          </label>
                          <label className="col-span-full flex flex-col gap-1 text-xs">
                            <span className="text-muted-foreground">
                              Description (optional — auto-filled if left blank)
                            </span>
                            <Input className="h-8" value={newRule.description}
                                   placeholder={CHECK_DEFS[newRule.check].describe(newRule)}
                                   onChange={(e) => setNewRule({ ...newRule, description: e.target.value })} />
                          </label>
                        </div>
                        <p className="my-2 text-xs text-muted-foreground">
                          {CHECK_DEFS[newRule.check].hint}
                        </p>
                        <Button size="sm" disabled={!ruleFieldReady()} onClick={addRule}>
                          <Plus /> Add rule
                        </Button>
                      </CardContent>
                    </Card>

                    <Disclosure summary="view generated YAML (read-only)">
                      <Pre className="text-[11px]">{rulesDraft}</Pre>
                    </Disclosure>
                  </>
                ) : (
                  <>
                    {rulesParseError && (
                      <p className="my-2 text-sm text-destructive">
                        Couldn't read this as rule cards: {rulesParseError}. Fix
                        the YAML below, or your edits still save as-is.
                      </p>
                    )}
                    <Textarea className="my-2 font-mono text-xs" rows={18} spellCheck={false}
                              value={rulesDraft}
                              onChange={(e) => { setRulesDraft(e.target.value); setRulesDirty(true); }} />
                  </>
                )}

                <div className="mt-3 flex flex-wrap gap-2">
                  <Button size="sm" disabled={rulesBusy || !rulesDirty} onClick={saveRules}>
                    <Save /> Save rules for this pile
                  </Button>
                  <Button size="sm" variant="outline"
                          disabled={rulesBusy || rules.source !== "pile"}
                          onClick={resetRules}>
                    <RotateCcw /> Reset to system default
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
        </section>
      )}

      {/* ------------------------------------------------ review tab */}
      {tab === "review" && (
        <section>
          {run ? (
            <>
              <div className="my-3 flex flex-wrap items-center gap-3">
                <StatusChip status={run.status} />
                <span className="text-sm">
                  {items.length} item(s) · {pending} still need a decision
                </span>
                <Button size="sm" className="ml-auto"
                        disabled={pending > 0 || run.status !== "awaiting_review"}
                        onClick={resume}>
                  {run.status !== "awaiting_review" ? "nothing to resume"
                    : pending > 0 ? `decide ${pending} more to resume`
                    : <><Play /> Resume run</>}
                </Button>
              </div>
              {run.status === "awaiting_review" && (
                <p className="my-2 text-sm text-muted-foreground">
                  Nothing is saved yet. Approve or reject every item below —
                  rejected items are dropped, approved ones land in the report
                  when you resume.
                </p>
              )}
              {items.map((i) => <ItemCard key={i.id} item={i} onDecide={decide} />)}
              {items.length === 0 && (
                <EmptyState Icon={CirclePause} title="This run produced no review items."
                            sub="Nothing needed a human decision." />
              )}
            </>
          ) : (
            <EmptyState Icon={SearchCheck} title="No run selected"
                        sub="Run an analysis first — it will pause here for your review." />
          )}
        </section>
      )}

      {tab === "timeline" && (run
        ? <Activity events={run.events} />
        : <EmptyState Icon={SearchCheck} title="No run selected"
                      sub="Run an analysis first — every step it takes lands here, in order." />)}

      {/* ------------------------------------------------ register tab */}
      {tab === "register" && register && (
        <section>
          {register.sections.length > 0 && (
            <div className="my-3 flex flex-wrap items-center gap-3">
              <span className="text-sm text-muted-foreground">
                Every value below cites its source — click a card to see the exact wording.
              </span>
              <Button size="sm" variant="outline" className="ml-auto" onClick={copyRegister}>
                <Copy /> Copy as markdown
              </Button>
            </div>
          )}
          {register.sections.map((s) => (
            <Card className="my-2.5 gap-2" key={s.section_key}>
              <CardHeader>
                <CardTitleText>{s.title}</CardTitleText>
                <span className="text-xs text-muted-foreground">
                  {REASON_LABELS[s.updated_reason] || s.updated_reason}
                </span>
                <span className="ml-auto inline-flex items-center gap-1 font-mono text-xs text-muted-foreground"
                      title="Changes only when this section's content changes — proof nothing here was silently rewritten.">
                  <Fingerprint className="size-3.5" />
                  {s.content_hash.slice(0, 10)}…
                </span>
              </CardHeader>
              <CardContent>
                <Markdown text={stripLeadingHeading(s.content_md)} />
              </CardContent>
            </Card>
          ))}
          {register.sections.length === 0 && (
            <EmptyState Icon={FilePen} title="Report is empty"
                        sub="Run and approve an analysis — approved content lands here." />
          )}
        </section>
      )}

      {/* ------------------------------------------------ findings tab */}
      {tab === "findings" && (
        <section>
          {findings.map((f) => {
            const sev = sevMeta(f.severity);
            return (
              <Card className={cn("my-2.5 gap-2", CARD_EDGE[f.status])} key={f.id}>
                <CardHeader className="flex-nowrap items-start">
                  <IconTile Icon={sev.Icon} cls={sev.cls} bg={sev.bg} />
                  <div className="min-w-0 leading-snug">
                    <b className="block text-[14.5px]">{ruleTitle(f.rule_id)}</b>
                    <span className="text-xs text-muted-foreground">
                      {sev.label}{f.entity ? ` · ${f.entity}` : ""}
                    </span>
                  </div>
                  <ItemStatusBadge status={f.status} />
                </CardHeader>
                <CardContent>
                  <p className="text-sm">{humanizeText(f.message)}</p>
                  {f.source && (
                    <Disclosure summary="show source">
                      <p className="mt-1 text-sm text-muted-foreground">
                        {f.source}
                        {f.anchor && ` (position ${f.anchor[0]}–${f.anchor[1]})`}
                      </p>
                    </Disclosure>
                  )}
                </CardContent>
              </Card>
            );
          })}
          {findings.length === 0 && (
            <EmptyState Icon={CircleCheckBig} tone="ok" title="No issues found"
                        sub="The corpus is clean — an honest report of no findings." />
          )}
        </section>
      )}

      {/* ------------------------------------------------ provenance tab */}
      {tab === "provenance" && audit && (
        <section>
          <h3 className="mt-5 mb-2 text-[15px] font-semibold">
            Report sections — what changed, when, why
          </h3>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>section</TableHead><TableHead>last updated</TableHead>
                <TableHead>why</TableHead><TableHead>run type</TableHead>
                <TableHead>triggered by</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {audit.sections.map((s) => (
                <TableRow key={s.section_key}>
                  <TableCell className="font-medium">
                    {s.section_key === "overview" ? "Overview" : s.section_key.replace(/^client-/, "")}
                  </TableCell>
                  <TableCell>{s.updated_at.slice(0, 19).replace("T", " ")}</TableCell>
                  <TableCell>{REASON_LABELS[s.updated_reason] || s.updated_reason}</TableCell>
                  <TableCell>{RUN_KIND_LABELS[s.run_kind] || s.run_kind || ""}</TableCell>
                  <TableCell>{s.trigger_document || ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Disclosure summary="Show fingerprints (the byte-identity proof for untouched sections)">
            <Table>
              <TableHeader>
                <TableRow><TableHead>section</TableHead><TableHead>hash</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {audit.sections.map((s) => (
                  <TableRow key={s.section_key}>
                    <TableCell>{s.section_key}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {s.content_hash.slice(0, 16)}…
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Disclosure>

          <h3 className="mt-5 mb-2 text-[15px] font-semibold">
            Disagreements found ({audit.conflicts.length})
          </h3>
          {audit.conflicts.map((c, i) => (
            <Card className="my-2.5 gap-2" key={i}>
              <CardHeader className="flex-nowrap items-start">
                <IconTile Icon={TriangleAlert} cls="text-amber-600" bg="bg-amber-50" />
                <div className="min-w-0 leading-snug">
                  <b className="block text-[14.5px]">{c.entity}</b>
                  <span className="text-xs text-muted-foreground">{humanizeKey(c.key)}</span>
                </div>
                <ItemStatusBadge status={c.status} />
              </CardHeader>
              <CardContent>
                <p className="text-sm">{humanizeText(c.detail)}</p>
              </CardContent>
            </Card>
          ))}
          {audit.conflicts.length === 0 && (
            <p className="text-sm text-muted-foreground">No disagreements found.</p>
          )}

          <Disclosure summary={`Show every extracted fact (${audit.facts.length}) — for verifying provenance`}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>client</TableHead><TableHead>fact</TableHead>
                  <TableHead>value</TableHead><TableHead>source</TableHead>
                  <TableHead>position</TableHead><TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {audit.facts.map((f, i) => (
                  <TableRow key={i} className={f.superseded ? "opacity-55 line-through" : ""}>
                    <TableCell>{f.entity}</TableCell>
                    <TableCell>{humanizeKey(f.key)}</TableCell>
                    <TableCell>{f.value}</TableCell>
                    <TableCell>{f.source}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {f.anchor[0]}–{f.anchor[1]}
                    </TableCell>
                    <TableCell>{f.superseded ? "superseded" : ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Disclosure>
        </section>
      )}

      {/* ------------------------------------------------ costs tab */}
      {tab === "costs" && costs && (
        <section>
          <div className="my-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Total spent" value={`$${costs.total.usd.toFixed(4)}`}
                      sub="across all runs" accent />
            <StatTile label="Processing time"
                      value={`${(costs.total.latency_ms / 1000).toFixed(1)}s`}
                      sub="model + tool latency" />
            <StatTile label="AI reading + writing"
                      value={fmtCompact(costs.total.input_tokens + costs.total.output_tokens)}
                      sub="input + output tokens" />
            <StatTile label="Styled exports" value={costs.total.superdocs_ops}
                      sub="paid SuperDocs operation(s)" />
          </div>
          <Disclosure summary="Show the breakdown by run and stage">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>run</TableHead><TableHead>stage</TableHead>
                  <TableHead>provider</TableHead><TableHead>model</TableHead>
                  <TableHead>calls</TableHead><TableHead>in</TableHead>
                  <TableHead>out</TableHead><TableHead>ops</TableHead>
                  <TableHead>usd</TableHead><TableHead>ms</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {costs.by_stage.map((r, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {(r.run_id || "").slice(0, 8)}
                    </TableCell>
                    <TableCell>{r.stage}</TableCell>
                    <TableCell>{r.provider}</TableCell>
                    <TableCell>{r.model || ""}</TableCell>
                    <TableCell>{r.calls}</TableCell>
                    <TableCell>{r.input_tokens}</TableCell>
                    <TableCell>{r.output_tokens}</TableCell>
                    <TableCell>{r.superdocs_ops}</TableCell>
                    <TableCell>{r.usd.toFixed(4)}</TableCell>
                    <TableCell>{r.latency_ms}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Disclosure>
        </section>
      )}
    </div>
  );
}

// Card titles in this app are inline <b>-weight text sitting beside chips,
// not the block headings shadcn's CardTitle assumes.
function CardTitleText({ children }) {
  return <b className="text-sm font-semibold">{children}</b>;
}

function segCls(active) {
  return cn(
    "inline-flex cursor-pointer items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
    active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
  );
}

function ErrorBanner({ error, onDismiss }) {
  return (
    <Alert variant="destructive" className="my-2 cursor-pointer" onClick={onDismiss}
           title="Click to dismiss">
      <CircleAlert />
      <AlertTitle>Something went wrong</AlertTitle>
      <AlertDescription>{error}</AlertDescription>
    </Alert>
  );
}

function Header({ health }) {
  const live = health && health.llm_provider !== "mock";
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 py-4">
      <div className="flex items-center gap-3">
        <div className="grid size-10 place-items-center rounded-xl bg-gradient-to-br from-primary to-sky-600 text-white shadow-sm">
          <FileSearch className="size-5" />
        </div>
        <div>
          <h1 className="text-lg leading-tight font-bold tracking-tight">DocTask</h1>
          <span className="text-xs text-muted-foreground">The Analyst That Never Sleeps</span>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <details className="relative text-xs text-muted-foreground">
          <summary className="flex cursor-pointer list-none items-center gap-1 font-medium select-none hover:text-primary [&::-webkit-details-marker]:hidden">
            <Info className="size-3.5" /> What do these words mean?
          </summary>
          <div className="absolute top-full right-0 z-20 mt-2 w-[320px] max-w-[80vw] space-y-2 rounded-xl border bg-popover p-4 text-popover-foreground shadow-md">
            <div><b>Pile</b> — a set of related documents you're analyzing together.</div>
            <div><b>Run</b> — one pass of the analyst reading your documents and proposing changes.</div>
            <div><b>Rules / playbook</b> — the checklist this pile is examined against; yours if you've set one, the system default otherwise.</div>
            <div><b>Report</b> — the living register of obligations the analyst keeps up to date for you.</div>
            <div><b>Issue / finding</b> — something in your documents that breaks one of your rules.</div>
            <div><b>Disagreement / conflict</b> — two documents stating different values for the same thing.</div>
          </div>
        </details>
        {health && (
          <span className={cn(
                  "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold",
                  live ? CHIP_TONES.live : CHIP_TONES.idle)}
                title="Active LLM backend">
            <i className={cn("size-2 rounded-full bg-current", live && "pulse-dot")} />
            {live
              ? `live · ${health.llm_provider}${health.llm_model ? ` (${health.llm_model})` : ""}`
              : "mock mode (recorded corpus)"}
          </span>
        )}
      </div>
    </header>
  );
}
