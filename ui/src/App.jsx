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
const fmtTime = (iso) => (iso || "").slice(11, 19);
const fmtDate = (iso) => (iso || "").slice(0, 16).replace("T", " ");

function Badge({ children, color = "#456" }) {
  return <span className="badge" style={{ background: color }}>{children}</span>;
}

function StatusChip({ status }) {
  const cls = { running: "live", committing: "live", awaiting_review: "warn",
                completed: "ok", failed: "bad" }[status] || "idle";
  const label = status === "awaiting_review" ? "waiting for your review" : status;
  return <span className={`chip ${cls}`}><i />{label}</span>;
}

// ---------------------------------------------------------------- pipeline map
const STEPS = [
  { label: "Ingest", sub: "documents in the pile" },
  { label: "Analyze", sub: "classify · extract · ground · reconcile" },
  { label: "Human review", sub: "you approve or reject every item" },
  { label: "Commit", sub: "only approved content lands" },
  { label: "Render", sub: "SuperDocs docx + pdf" },
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
        if (i === 1 && state === "active" && lastEvent) sub = `stage: ${lastEvent.stage}`;
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
function ItemCard({ item, onDecide }) {
  const [feedback, setFeedback] = useState("");
  const p = item.payload;
  const decided = item.status !== "pending";
  return (
    <div className={`card ${item.status}`}>
      <div className="card-head">
        {item.item_type === "section_update" && (
          <>
            <Badge color="#2471a3">SECTION</Badge>
            <b>{p.section_key}</b>
            <span className="dim"> {p.reason}</span>
          </>
        )}
        {item.item_type === "conflict" && (
          <>
            <Badge color="#b03a2e">CONFLICT</Badge>
            <b>{p.entity} / {p.key}</b>
          </>
        )}
        {item.item_type === "finding" && (
          <>
            <Badge color={SEV[p.severity] || "#456"}>
              {p.rule_id} · {p.severity}
            </Badge>
            <b>{p.entity || ""}</b>
          </>
        )}
        <span className={`status ${item.status}`}>{item.status}</span>
      </div>
      {item.item_type === "section_update" && (
        <pre className="md">{p.content_md}</pre>
      )}
      {item.item_type === "conflict" && <p>{p.detail}</p>}
      {item.item_type === "finding" && (
        <p>{p.message}{p.quote && <><br /><i className="dim">"{p.quote}"</i></>}</p>
      )}
      {!decided && (
        <div className="actions">
          <input placeholder="feedback (optional)" value={feedback}
                 onChange={(e) => setFeedback(e.target.value)} />
          <button className="approve"
                  onClick={() => onDecide(item.id, true, feedback)}>Approve</button>
          <button className="reject"
                  onClick={() => onDecide(item.id, false, feedback)}>Reject</button>
        </div>
      )}
      {decided && item.feedback && <p className="dim">feedback: {item.feedback}</p>}
    </div>
  );
}

function Timeline({ events }) {
  return (
    <table className="timeline">
      <tbody>
        {events.map((e, i) => (
          <tr key={i}>
            <td className="dim">{fmtTime(e.ts)}</td>
            <td><b>{e.stage}</b></td>
            <td>
              <Badge color={
                e.decision.includes("fail") ? "#b03a2e" :
                e.decision.includes("retry") || e.decision.includes("quarantin")
                  ? "#d68910" : "#1e8449"}>
                {e.decision}
              </Badge>
            </td>
            <td className="dim detail">{JSON.stringify(e.detail).slice(0, 140)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const TABS = ["documents", "review", "timeline", "register", "findings",
              "provenance", "costs"];

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
          say("Run complete — the register is updated.");
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
      say(`${added.length} sample document(s) loaded — now click Run analysis.`);
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
      say("Resuming — approved items are being committed.");
      setTab("timeline");
      refresh();
    } catch (e) { setError(String(e.message || e)); }
  };

  const copyRegister = async () => {
    try {
      await navigator.clipboard.writeText(register.markdown);
      say("Register markdown copied to clipboard.");
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
          <label>Pile</label>
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
                {r.kind} · {fmtDate(r.started_at)} · {r.status}
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
        {["documents", "review", "timeline", "register", "findings",
          "provenance", "costs"].map((t) => (
          <button key={t}
                  className={(tab === t ? "active" : "")
                    + (t === "review" && pending > 0 ? " attention" : "")}
                  onClick={() => setTab(t)}>
            {t === "documents" ? `documents (${docs.length})`
              : t === "review" && pending > 0 ? `review (${pending})` : t}
          </button>
        ))}
      </nav>

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
                    <td>{d.format}</td>
                    <td>{d.doc_class || <span className="dim">pending analysis</span>}</td>
                    <td>{d.entity || ""}</td>
                    <td><span className={`status ${d.status}`}>{d.status}</span></td>
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
                  Nothing is committed yet. Approve or reject every item below —
                  rejected items are dropped, approved ones land in the register
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
        ? <Timeline events={run.events} />
        : <p className="dim">No run selected — run an analysis first.</p>)}

      {/* ------------------------------------------------ register tab */}
      {tab === "register" && register && (
        <section>
          {register.sections.length > 0 && (
            <div className="bar">
              <span className="dim">
                The living document — every value cites its source.
              </span>
              <button className="ghost" style={{ marginLeft: "auto" }}
                      onClick={copyRegister}>⧉ Copy markdown</button>
            </div>
          )}
          {register.sections.map((s) => (
            <div className="card" key={s.section_key}>
              <div className="card-head">
                <b>{s.title}</b>
                <span className="dim mono">sha256 {s.content_hash.slice(0, 16)}…</span>
                <span className="dim">{s.updated_reason}</span>
              </div>
              <pre className="md">{s.content_md}</pre>
            </div>
          ))}
          {register.sections.length === 0 &&
            <p className="dim">Register is empty — run and approve an analysis.</p>}
        </section>
      )}

      {/* ------------------------------------------------ findings tab */}
      {tab === "findings" && (
        <section>
          {findings.map((f) => (
            <div className={`card ${f.status}`} key={f.id}>
              <div className="card-head">
                <Badge color={SEV[f.severity] || "#456"}>{f.rule_id}</Badge>
                <b>{f.entity || ""}</b>
                <span className={`status ${f.status}`}>{f.status}</span>
              </div>
              <p>{f.message}</p>
              {f.source && <p className="dim">source: {f.source}
                {f.anchor && ` (chars ${f.anchor[0]}–${f.anchor[1]})`}</p>}
            </div>
          ))}
          {findings.length === 0 && <p className="dim">No findings.</p>}
        </section>
      )}

      {/* ------------------------------------------------ provenance tab */}
      {tab === "provenance" && audit && (
        <section>
          <h3>Sections — what changed, when, because of which source</h3>
          <table className="grid">
            <thead><tr><th>section</th><th>updated</th><th>reason</th>
              <th>run kind</th><th>trigger doc</th><th>hash</th></tr></thead>
            <tbody>
              {audit.sections.map((s) => (
                <tr key={s.section_key}>
                  <td>{s.section_key}</td>
                  <td>{s.updated_at.slice(0, 19)}</td>
                  <td>{s.updated_reason}</td>
                  <td>{s.run_kind || ""}</td>
                  <td>{s.trigger_document || ""}</td>
                  <td className="mono dim">{s.content_hash.slice(0, 12)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>Conflicts</h3>
          {audit.conflicts.map((c, i) => (
            <div className="card" key={i}>
              <div className="card-head">
                <Badge color="#b03a2e">{c.entity} / {c.key}</Badge>
                <span className={`status ${c.status}`}>{c.status}</span>
              </div>
              <p>{c.detail}</p>
            </div>
          ))}
          {audit.conflicts.length === 0 && <p className="dim">No conflicts.</p>}
          <h3>Facts ({audit.facts.length})</h3>
          <table className="grid">
            <thead><tr><th>client</th><th>key</th><th>value</th>
              <th>source</th><th>anchor</th><th></th></tr></thead>
            <tbody>
              {audit.facts.map((f, i) => (
                <tr key={i} className={f.superseded ? "superseded" : ""}>
                  <td>{f.entity}</td><td>{f.key}</td><td>{f.value}</td>
                  <td>{f.source}</td>
                  <td className="mono dim">{f.anchor[0]}–{f.anchor[1]}</td>
                  <td>{f.superseded ? "superseded" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* ------------------------------------------------ costs tab */}
      {tab === "costs" && costs && (
        <section>
          <div className="bar">
            <Badge>total ${costs.total.usd.toFixed(4)}</Badge>
            <Badge>{costs.total.input_tokens + costs.total.output_tokens} tokens</Badge>
            <Badge>{costs.total.superdocs_ops} SuperDocs ops</Badge>
            <Badge>{(costs.total.latency_ms / 1000).toFixed(1)}s total latency</Badge>
          </div>
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
      {health && (
        <span className={`chip ${live ? "live" : "idle"}`} title="Active LLM backend">
          <i />
          {live
            ? `live · ${health.llm_provider}${health.llm_model ? ` (${health.llm_model})` : ""}`
            : "mock mode (recorded corpus)"}
        </span>
      )}
    </header>
  );
}
