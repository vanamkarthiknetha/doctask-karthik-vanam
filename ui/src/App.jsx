import React, { useCallback, useEffect, useRef, useState } from "react";

// GET when no body; POST json when body given (pass {} for empty POST).
const api = async (path, body) => {
  const r = await fetch(path, body && {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }
  return r.json();
};

const SEV = { high: "#c0392b", medium: "#d68910", low: "#7f8c8d" };
const SEV_ICON = { high: "🔴", medium: "🟠", low: "⚪" };
const SEV_LABEL = { high: "High priority", medium: "Needs attention", low: "Minor note" };
const fmtTime = (iso) => (iso || "").slice(11, 19);
const fmtDate = (iso) => (iso || "").slice(0, 16).replace("T", " ");

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
const FORMAT_ICON = { md: "📄", txt: "📄", html: "🌐", docx: "📝", pdf: "📕" };
const STAGE_LABEL = {
  classify: "reading documents", extract: "pulling out facts",
  ground: "double-checking facts", reconcile: "comparing documents",
  compose: "drafting updates", examine: "checking your rules",
  propose: "preparing your review", commit: "saving decisions", render: "exporting",
};
const STAGE_ICON = {
  classify: "🏷️", extract: "📤", ground: "✅",
  reconcile: "🔀", compose: "✍️", examine: "🔍",
  propose: "⏸️", commit: "💾", render: "🖨️",
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
      return `Checked ${d.rules_evaluated} playbook rule(s) — ${d.new_findings} new issue(s).`;
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
// shape, and never touches innerHTML.
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
        ? <p className="dim" key={i}>Nothing extracted yet.</p>
        : (
          <table className="grid md-table" key={i}>
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

function Badge({ children, color = "#456" }) {
  return <span className="badge" style={{ background: color }}>{children}</span>;
}

function StatusChip({ status }) {
  const cls = { running: "live", committing: "live", awaiting_review: "warn",
                completed: "ok", failed: "bad" }[status] || "idle";
  return <span className={`chip ${cls}`}><i />{STATUS_LABELS[status] || status}</span>;
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
    <div className="stepper">
      {STEPS.map((s, i) => {
        const state = i < active ? "done" : i === active ? "active" : "todo";
        let sub = s.sub;
        if (i === 1 && state === "active" && lastEvent) {
          const stage = lastEvent.stage.split(":")[0];
          sub = `right now: ${STAGE_LABEL[stage] || stage}`;
        }
        if (i === 2 && state === "active") sub = `${pending} item(s) waiting for you`;
        return (
          <div className={`step ${state}`} key={s.label}>
            <div className="dot">{state === "done" ? "✓" : i + 1}</div>
            <div className="step-txt">
              <b>{s.label}</b>
              <span>{sub}</span>
            </div>
            {i < STEPS.length - 1 && <div className="conn" />}
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
    return { icon: "📝", title: `Update: ${p.title}`,
             sub: `Why: ${REASON_LABELS[p.reason] || p.reason || "content changed"}` };
  }
  if (item.item_type === "conflict") {
    return { icon: "⚠️", title: `Disagreement: ${p.entity}`,
             sub: humanizeKey(p.key) };
  }
  return { icon: SEV_ICON[p.severity] || "🔍", title: ruleTitle(p.rule_id),
           sub: `${SEV_LABEL[p.severity] || p.severity}${p.entity ? ` · ${p.entity}` : ""}` };
}

function ItemCard({ item, onDecide }) {
  const [feedback, setFeedback] = useState("");
  const p = item.payload;
  const decided = item.status !== "pending";
  const h = itemHeadline(item);
  return (
    <div className={`card item-card ${item.status}`}>
      <div className="card-head">
        <span className="item-icon" aria-hidden="true">{h.icon}</span>
        <div className="item-title">
          <b>{h.title}</b>
          <span className="dim">{h.sub}</span>
        </div>
        <span className={`status ${item.status}`}>{item.status}</span>
      </div>

      {item.item_type === "section_update" && (
        <>
          <div className="md-preview"><Markdown text={stripLeadingHeading(p.content_md)} /></div>
          <details className="raw-toggle">
            <summary>View raw text (for exact diffing)</summary>
            <pre className="md">{p.content_md}</pre>
          </details>
        </>
      )}
      {item.item_type === "conflict" && <p>{humanizeText(p.detail)}</p>}
      {item.item_type === "finding" && (
        <>
          <p>{humanizeText(p.message)}</p>
          {p.quote && <blockquote className="evidence">“{p.quote}”</blockquote>}
          <p className="dim rule-tag">rule {p.rule_id}</p>
        </>
      )}

      {!decided && (
        <div className="actions">
          <input placeholder="Add a note (optional)" value={feedback}
                 onChange={(e) => setFeedback(e.target.value)} />
          <button className="approve"
                  onClick={() => onDecide(item.id, true, feedback)}>✓ Approve</button>
          <button className="reject"
                  onClick={() => onDecide(item.id, false, feedback)}>✗ Reject</button>
        </div>
      )}
      {decided && item.feedback && <p className="dim">Note: {item.feedback}</p>}
    </div>
  );
}

function Activity({ events }) {
  return (
    <div className="activity">
      {events.map((e, i) => {
        const plain = describeEvent(e);
        const tone = eventTone(e.decision);
        const stage = e.stage.split(":")[0];
        return (
          <div className={`activity-row ${tone}`} key={i}>
            <span className="activity-icon" aria-hidden="true">{STAGE_ICON[stage] || "•"}</span>
            <div className="activity-body">
              <div className="activity-line">
                <b>{plain || `${e.stage} — ${e.decision}`}</b>
                <span className="dim mono">{fmtTime(e.ts)}</span>
              </div>
              {!plain && <span className="dim">stage: {e.stage}</span>}
              <details>
                <summary className="dim">technical detail</summary>
                <pre className="md small">{JSON.stringify(e.detail, null, 2)}</pre>
              </details>
            </div>
          </div>
        );
      })}
    </div>
  );
}

const TABS = ["documents", "review", "timeline", "register", "findings",
              "provenance", "costs"];
const TAB_LABELS = {
  documents: "Documents", review: "Review", timeline: "Activity",
  register: "Report", findings: "Issues", provenance: "History", costs: "Cost",
};
const TAB_CAPTIONS = {
  documents: "Every source file the analyst has read so far.",
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
  const [health, setHealth] = useState(null);
  const [sampleSets, setSampleSets] = useState([]);
  const [tab, setTab] = useState(() => {
    const h = window.location.hash.slice(1);
    return TABS.includes(h) ? h : "documents";
  });
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState(false);
  const [newPile, setNewPile] = useState("");
  const [creating, setCreating] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const prevStatus = useRef({});
  const fileInput = useRef(null);
  const toastTimer = useRef(null);

  const say = (msg) => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 4000);
  };

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
      const [reg, aud, fnd, cst] = await Promise.all([
        api(`/piles/${pid}/register`), api(`/piles/${pid}/audit`),
        api(`/piles/${pid}/findings`), api(`/piles/${pid}/costs`),
      ]);
      setRegister(reg); setAudit(aud); setFindings(fnd); setCosts(cst);
      setError("");
    } catch (e) {
      setError(String(e.message || e));
    }
  }, [pileId, runSel, sampleSets.length]);

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
      <div className="wrap">
        <Header health={health} />
        <div className="hero card">
          <h2>Welcome — create your first pile</h2>
          <p className="dim">
            A <b>pile</b> is an isolated set of documents (contracts, amendments,
            invoices) that the analyst turns into a living obligations register.
          </p>
          <div className="actions">
            <input autoFocus placeholder="pile name, e.g. meridian-clients"
                   value={newPile} onChange={(e) => setNewPile(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && createPile()} />
            <button className="primary" onClick={createPile}>Create pile</button>
          </div>
        </div>
        {error && <div className="error">{error}</div>}
      </div>
    );
  }

  return (
    <div className="wrap">
      <Header health={health} />

      {/* ------------------------------------------------ control bar */}
      <div className="controls card">
        <div className="ctl-group">
          <label title="A pile is an isolated set of related documents.">Pile</label>
          {creating ? (
            <span className="newpile">
              <input autoFocus placeholder="new pile name" value={newPile}
                     onChange={(e) => setNewPile(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && createPile()} />
              <button className="primary" onClick={createPile}>Create</button>
              <button className="ghost" onClick={() => setCreating(false)}>✕</button>
            </span>
          ) : (
            <>
              <select value={pileId || ""}
                      onChange={(e) => {
                        setPileId(e.target.value);
                        setRunSel({ id: null, manual: false });
                      }}>
                {(piles || []).map((p) =>
                  <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <button className="ghost" title="Create a new pile"
                      onClick={() => setCreating(true)}>+ New pile</button>
            </>
          )}
        </div>

        <div className="ctl-group grow">
          <button className="ghost" disabled={busy}
                  onClick={() => fileInput.current.click()}>
            ⬆ Upload documents
          </button>
          <input ref={fileInput} type="file" multiple hidden
                 accept=".md,.txt,.html,.docx,.pdf"
                 onChange={(e) => { uploadFiles([...e.target.files]); e.target.value = ""; }} />
          <button className="primary" disabled={busy || !docs.length || !!activeRun}
                  title={!docs.length ? "Add documents first"
                        : activeRun ? "A run is already in progress" : ""}
                  onClick={startRun}>
            ▶ Run analysis
          </button>
        </div>

        <div className="ctl-group">
          <label>Run</label>
          <select value={runSel.id || ""}
                  onChange={(e) => setRunSel({ id: e.target.value, manual: true })}>
            {!runs.length && <option value="">no runs yet</option>}
            {[...runs].reverse().map((r) => (
              <option key={r.id} value={r.id}>
                {RUN_KIND_LABELS[r.kind] || r.kind} · {fmtDate(r.started_at)} · {RUN_STATUS_SHORT[r.status] || r.status}
              </option>
            ))}
          </select>
          {run && <StatusChip status={run.status} />}
        </div>
      </div>

      <Stepper run={run} docsCount={docs.length} pending={pending} />

      {error && <div className="error" onClick={() => setError("")}>{error}</div>}
      {toast && <div className="toast">{toast}</div>}
      {run && run.status === "failed" && (
        <div className="error">Run failed: {run.error}</div>
      )}

      <nav>
        {TABS.map((t) => (
          <button key={t}
                  className={(tab === t ? "active" : "")
                    + (t === "review" && pending > 0 ? " attention" : "")}
                  onClick={() => setTab(t)}>
            {t === "documents" ? `${TAB_LABELS[t]} (${docs.length})`
              : t === "review" && pending > 0 ? `${TAB_LABELS[t]} (${pending})` : TAB_LABELS[t]}
          </button>
        ))}
      </nav>
      <p className="dim tab-caption">{TAB_CAPTIONS[tab]}</p>

      {/* ------------------------------------------------ documents tab */}
      {tab === "documents" && (
        <section>
          <div className={`dropzone ${dragOver ? "over" : ""}`}
               onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
               onDragLeave={() => setDragOver(false)}
               onDrop={onDrop}
               onClick={() => fileInput.current.click()}>
            <b>Drop files here</b> or click to browse
            <span className="dim"> — md · txt · html · docx · pdf</span>
          </div>
          {docs.length === 0 && sampleSets.length > 0 && (
            <div className="card samples">
              <b>…or try the bundled sample corpus</b>
              <p className="dim">
                Fictional voice-AI vendor "Meridian Voice Systems": client
                contracts, amendments and invoices with planted conflicts,
                a compliance breach and a prompt-injection attempt.
              </p>
              <div className="actions">
                {sampleSets.map((s) => (
                  <button key={s.name} disabled={busy}
                          className={s.name === "seed" ? "primary" : "ghost"}
                          onClick={() => loadSamples(s.name)}>
                    Load "{s.name}" ({s.files.length} files)
                  </button>
                ))}
              </div>
            </div>
          )}
          {docs.length > 0 && (
            <table className="grid">
              <thead><tr><th>file</th><th>format</th><th>class</th>
                <th>client</th><th>status</th><th></th></tr></thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td>{d.filename}</td>
                    <td>{FORMAT_ICON[d.format] || ""} {d.format}</td>
                    <td>{d.doc_class || <span className="dim">pending analysis</span>}</td>
                    <td>{d.entity || ""}</td>
                    <td><span className={`status ${d.status}`}>{STATUS_LABELS[d.status] || d.status}</span></td>
                    <td>{d.injection_flagged &&
                      <Badge color="#b03a2e">⚠ injection flagged</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {pile && (
            <p className="dim hint">
              Watched folder: files dropped into <code>corpus/incoming/{pile.name}/</code>{" "}
              are ingested automatically as focused update runs — no clicks needed.
            </p>
          )}
        </section>
      )}

      {/* ------------------------------------------------ review tab */}
      {tab === "review" && (
        <section>
          {run ? (
            <>
              <div className="bar">
                <StatusChip status={run.status} />
                <span>{items.length} item(s) · {pending} still need a decision</span>
                <button className="resume"
                        disabled={pending > 0 || run.status !== "awaiting_review"}
                        onClick={resume}>
                  {run.status !== "awaiting_review" ? "nothing to resume"
                    : pending > 0 ? `decide ${pending} more to resume`
                    : "▶ Resume run"}
                </button>
              </div>
              {run.status === "awaiting_review" && (
                <p className="dim">
                  Nothing is saved yet. Approve or reject every item below —
                  rejected items are dropped, approved ones land in the report
                  when you resume.
                </p>
              )}
              {items.map((i) => <ItemCard key={i.id} item={i} onDecide={decide} />)}
              {items.length === 0 &&
                <p className="dim">This run produced no review items.</p>}
            </>
          ) : <p className="dim">No run selected — run an analysis first.</p>}
        </section>
      )}

      {tab === "timeline" && (run
        ? <Activity events={run.events} />
        : <p className="dim">No run selected — run an analysis first.</p>)}

      {/* ------------------------------------------------ register tab */}
      {tab === "register" && register && (
        <section>
          {register.sections.length > 0 && (
            <div className="bar">
              <span className="dim">
                Every value below cites its source — click a card to see the exact wording.
              </span>
              <button className="ghost" style={{ marginLeft: "auto" }}
                      onClick={copyRegister}>⧉ Copy as markdown</button>
            </div>
          )}
          {register.sections.map((s) => (
            <div className="card" key={s.section_key}>
              <div className="card-head">
                <b>{s.title}</b>
                <span className="dim">{REASON_LABELS[s.updated_reason] || s.updated_reason}</span>
                <span className="dim mono" title="Changes only when this section's content changes — proof nothing here was silently rewritten.">
                  fingerprint {s.content_hash.slice(0, 10)}…
                </span>
              </div>
              <Markdown text={stripLeadingHeading(s.content_md)} />
            </div>
          ))}
          {register.sections.length === 0 &&
            <p className="dim">Report is empty — run and approve an analysis.</p>}
        </section>
      )}

      {/* ------------------------------------------------ findings tab */}
      {tab === "findings" && (
        <section>
          {findings.map((f) => (
            <div className={`card ${f.status}`} key={f.id}>
              <div className="card-head">
                <span className="item-icon" aria-hidden="true">{SEV_ICON[f.severity] || "🔍"}</span>
                <div className="item-title">
                  <b>{ruleTitle(f.rule_id)}</b>
                  <span className="dim">{SEV_LABEL[f.severity] || f.severity}{f.entity ? ` · ${f.entity}` : ""}</span>
                </div>
                <span className={`status ${f.status}`}>{f.status}</span>
              </div>
              <p>{humanizeText(f.message)}</p>
              {f.source && (
                <details className="raw-toggle">
                  <summary>show source</summary>
                  <p className="dim">{f.source}
                    {f.anchor && ` (position ${f.anchor[0]}–${f.anchor[1]})`}</p>
                </details>
              )}
            </div>
          ))}
          {findings.length === 0 && <p className="dim">No issues found — the corpus is clean.</p>}
        </section>
      )}

      {/* ------------------------------------------------ provenance tab */}
      {tab === "provenance" && audit && (
        <section>
          <h3>Report sections — what changed, when, why</h3>
          <table className="grid">
            <thead><tr><th>section</th><th>last updated</th><th>why</th>
              <th>run type</th><th>triggered by</th></tr></thead>
            <tbody>
              {audit.sections.map((s) => (
                <tr key={s.section_key}>
                  <td>{s.section_key === "overview" ? "Overview" : s.section_key.replace(/^client-/, "")}</td>
                  <td>{s.updated_at.slice(0, 19).replace("T", " ")}</td>
                  <td>{REASON_LABELS[s.updated_reason] || s.updated_reason}</td>
                  <td>{RUN_KIND_LABELS[s.run_kind] || s.run_kind || ""}</td>
                  <td>{s.trigger_document || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <details className="raw-toggle">
            <summary>Show fingerprints (the byte-identity proof for untouched sections)</summary>
            <table className="grid">
              <thead><tr><th>section</th><th>hash</th></tr></thead>
              <tbody>
                {audit.sections.map((s) => (
                  <tr key={s.section_key}>
                    <td>{s.section_key}</td>
                    <td className="mono dim">{s.content_hash.slice(0, 16)}…</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>

          <h3>Disagreements found ({audit.conflicts.length})</h3>
          {audit.conflicts.map((c, i) => (
            <div className="card" key={i}>
              <div className="card-head">
                <span className="item-icon" aria-hidden="true">⚠️</span>
                <div className="item-title"><b>{c.entity}</b><span className="dim">{humanizeKey(c.key)}</span></div>
                <span className={`status ${c.status}`}>{c.status}</span>
              </div>
              <p>{humanizeText(c.detail)}</p>
            </div>
          ))}
          {audit.conflicts.length === 0 && <p className="dim">No disagreements found.</p>}

          <details className="raw-toggle">
            <summary>Show every extracted fact ({audit.facts.length}) — for verifying provenance</summary>
            <table className="grid">
              <thead><tr><th>client</th><th>fact</th><th>value</th>
                <th>source</th><th>position</th><th></th></tr></thead>
              <tbody>
                {audit.facts.map((f, i) => (
                  <tr key={i} className={f.superseded ? "superseded" : ""}>
                    <td>{f.entity}</td><td>{humanizeKey(f.key)}</td><td>{f.value}</td>
                    <td>{f.source}</td>
                    <td className="mono dim">{f.anchor[0]}–{f.anchor[1]}</td>
                    <td>{f.superseded ? "superseded" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </section>
      )}

      {/* ------------------------------------------------ costs tab */}
      {tab === "costs" && costs && (
        <section>
          <div className="cost-hero card">
            <div className="cost-num">${costs.total.usd.toFixed(4)}</div>
            <div className="dim">total spent across all runs</div>
            <div className="cost-sub">
              {(costs.total.latency_ms / 1000).toFixed(1)}s of processing time
              {" · "}{(costs.total.input_tokens + costs.total.output_tokens).toLocaleString()} words of AI reading/writing
              {" · "}{costs.total.superdocs_ops} paid export operation(s)
            </div>
          </div>
          <details className="raw-toggle">
            <summary>Show the breakdown by run and stage</summary>
            <table className="grid">
              <thead><tr><th>run</th><th>stage</th><th>provider</th><th>model</th>
                <th>calls</th><th>in</th><th>out</th><th>ops</th><th>usd</th>
                <th>ms</th></tr></thead>
              <tbody>
                {costs.by_stage.map((r, i) => (
                  <tr key={i}>
                    <td className="mono dim">{(r.run_id || "").slice(0, 8)}</td>
                    <td>{r.stage}</td><td>{r.provider}</td><td>{r.model || ""}</td>
                    <td>{r.calls}</td><td>{r.input_tokens}</td>
                    <td>{r.output_tokens}</td><td>{r.superdocs_ops}</td>
                    <td>{r.usd.toFixed(4)}</td><td>{r.latency_ms}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </section>
      )}
    </div>
  );
}

function Header({ health }) {
  const live = health && health.llm_provider !== "mock";
  return (
    <header>
      <div className="brand">
        <div className="logo">DT</div>
        <div>
          <h1>DocTask</h1>
          <span className="dim">The Analyst That Never Sleeps</span>
        </div>
      </div>
      <div className="header-right">
        <details className="glossary">
          <summary>What do these words mean?</summary>
          <div className="glossary-panel">
            <div><b>Pile</b> — a set of related documents you're analyzing together.</div>
            <div><b>Run</b> — one pass of the analyst reading your documents and proposing changes.</div>
            <div><b>Report</b> — the living register of obligations the analyst keeps up to date for you.</div>
            <div><b>Issue / finding</b> — something in your documents that breaks one of your rules.</div>
            <div><b>Disagreement / conflict</b> — two documents stating different values for the same thing.</div>
          </div>
        </details>
        {health && (
          <span className={`chip ${live ? "live" : "idle"}`} title="Active LLM backend">
            <i />
            {live
              ? `live · ${health.llm_provider}${health.llm_model ? ` (${health.llm_model})` : ""}`
              : "mock mode (recorded corpus)"}
          </span>
        )}
      </div>
    </header>
  );
}
