import React, { useCallback, useEffect, useState } from "react";

const api = async (path, opts) => {
  const r = await fetch(path, opts && {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts),
  });
  if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
  return r.json();
};

const SEV = { high: "#c0392b", medium: "#d68910", low: "#7f8c8d" };

function Badge({ children, color = "#456" }) {
  return <span className="badge" style={{ background: color }}>{children}</span>;
}

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
            <td className="dim">{e.ts.slice(11, 19)}</td>
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

export default function App() {
  const [piles, setPiles] = useState([]);
  const [pileId, setPileId] = useState(null);
  const [runs, setRuns] = useState([]);
  const [runId, setRunId] = useState(null);
  const [items, setItems] = useState([]);
  const [register, setRegister] = useState(null);
  const [audit, setAudit] = useState(null);
  const [findings, setFindings] = useState([]);
  const [costs, setCosts] = useState(null);
  const [tab, setTab] = useState("review");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const ps = await api("/piles");
      setPiles(ps);
      const pid = pileId || (ps[0] && ps[0].id);
      if (!pid) return;
      setPileId(pid);
      const rs = await api(`/piles/${pid}/runs`);
      setRuns(rs);
      const rid = runId || (rs.length && rs[rs.length - 1].id);
      if (rid) {
        setRunId(rid);
        setItems(await api(`/runs/${rid}/pending?status=`));
      }
      setRegister(await api(`/piles/${pid}/register`));
      setAudit(await api(`/piles/${pid}/audit`));
      setFindings(await api(`/piles/${pid}/findings`));
      setCosts(await api(`/piles/${pid}/costs`));
      setError("");
    } catch (e) {
      setError(String(e.message || e));
    }
  }, [pileId, runId]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  const decide = async (id, approve, feedback) => {
    try {
      await api(`/items/${id}/decision`,
        { approve, decided_by: "review-ui", feedback: feedback || null });
      refresh();
    } catch (e) { setError(String(e.message || e)); }
  };

  const run = runs.find((r) => r.id === runId);
  const pending = items.filter((i) => i.status === "pending").length;

  return (
    <div className="wrap">
      <header>
        <h1>DocTask <span className="dim">— The Analyst That Never Sleeps</span></h1>
        <div className="selectors">
          <select value={pileId || ""} onChange={(e) => { setPileId(e.target.value); setRunId(null); }}>
            {piles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <select value={runId || ""} onChange={(e) => setRunId(e.target.value)}>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.kind} · {r.started_at.slice(0, 16)} · {r.status}
              </option>
            ))}
          </select>
        </div>
      </header>
      {error && <div className="error">{error}</div>}

      <nav>
        {["review", "timeline", "register", "findings", "provenance", "costs"].map((t) => (
          <button key={t} className={tab === t ? "active" : ""}
                  onClick={() => setTab(t)}>
            {t}{t === "review" && pending > 0 ? ` (${pending})` : ""}
          </button>
        ))}
      </nav>

      {tab === "review" && run && (
        <section>
          <div className="bar">
            <Badge color={run.status === "awaiting_review" ? "#d68910" :
                          run.status === "completed" ? "#1e8449" : "#456"}>
              run {run.status}
            </Badge>
            <span>{items.length} items · {pending} pending</span>
            <button className="resume" disabled={pending > 0 || run.status !== "awaiting_review"}
                    onClick={async () => { await api(`/runs/${runId}/resume?wait=false`, {}); refresh(); }}>
              {pending > 0 ? `decide ${pending} more to resume` : "Resume run"}
            </button>
          </div>
          {items.map((i) => <ItemCard key={i.id} item={i} onDecide={decide} />)}
          {items.length === 0 && <p className="dim">No review items for this run.</p>}
        </section>
      )}

      {tab === "timeline" && run && <Timeline events={run.events} />}

      {tab === "register" && register && (
        <section>
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
          <h3>Facts ({audit.facts.length})</h3>
          <table className="grid">
            <thead><tr><th>entity</th><th>key</th><th>value</th>
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
