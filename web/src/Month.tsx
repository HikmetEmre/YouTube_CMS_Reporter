import { ReactNode, useRef, useState } from "react";
import { api, Meta, Month, SettlementLine, StepState, Vertical } from "./api";
import { BarList, TrendChart } from "./charts";
import { Avatar, Button, compactUsd, Empty, Icon, Loading, local, Modal, percent, Status, usd, useData } from "./ui";

type StepKey = "upload" | "match" | "review" | "send";
type Toast = (message: string, kind?: "good" | "bad") => void;
const ORDER: StepKey[] = ["upload", "match", "review", "send"];

function headline(month: Month): { text: string; go: StepKey; action: string } {
  const { steps, totals } = month;
  if (steps.upload !== "done")
    return { text: "Start by adding this month's CMS exports.", go: "upload", action: "Add files" };
  if (totals.unidentified_count)
    return {
      text: `${totals.unidentified_count} ${totals.unidentified_count === 1 ? "asset" : "assets"} worth ${usd(totals.unidentified)} still ${totals.unidentified_count === 1 ? "needs" : "need"} a client. Everything else is matched.`,
      go: "match",
      action: "Match them",
    };
  if (!month.fx_rate)
    return { text: "Everything is matched. Add the payout you received to convert the payments.", go: "upload", action: "Add payout" };
  if (steps.send === "attention")
    return { text: "The figures changed after the client packages were prepared. Prepare them again before sending.", go: "send", action: "Update packages" };
  if (steps.send !== "done")
    return { text: `Everything is matched. ${usd(totals.payment)} is owed to ${totals.clients} clients.`, go: "send", action: "Prepare packages" };
  return { text: `This month is ready: ${totals.clients} client packages are prepared.`, go: "send", action: "View packages" };
}

function stepNote(key: StepKey, month: Month): string {
  const files = Object.values(month.files).reduce((count, list) => count + list.length, 0);
  if (key === "upload") return files ? `${files} files` : "No files yet";
  if (key === "match") return month.totals.unidentified_count ? `${month.totals.unidentified_count} to match` : files ? "All matched" : "Waiting for files";
  if (key === "review") return files ? compactUsd(month.totals.revenue) : "Waiting for files";
  if (!month.packages) return "Not prepared yet";
  return month.packages.stale ? "Out of date" : `Prepared ${month.packages.generated_at.slice(5)}`;
}

const TITLES: Record<StepKey, string> = { upload: "Add files", match: "Match assets", review: "Review", send: "Send" };

function Stepper({ month, active, onPick }: { month: Month; active: StepKey; onPick: (step: StepKey) => void }) {
  return (
    <ol className="stepper">
      {ORDER.map((key, index) => {
        const state: StepState = month.steps[key];
        return (
          <li key={key}>
            <button type="button" className={`step step-${state}${key === active ? " step-active" : ""}`} onClick={() => onPick(key)} aria-current={key === active ? "step" : undefined}>
              <span className="step-badge">{state === "done" ? <Icon name="check" size={16} /> : state === "attention" ? <Icon name="alert" size={16} /> : index + 1}</span>
              <span className="step-text">
                <strong>{TITLES[key]}</strong>
                <small>{stepNote(key, month)}</small>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------------ step 1 */

function DropZone({ onFiles, busy }: { onFiles: (files: File[]) => void; busy: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div className={`dropzone${over ? " dropzone-over" : ""}`}
      onDragOver={(event) => { event.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => { event.preventDefault(); setOver(false); onFiles(Array.from(event.dataTransfer.files)); }}>
      {busy ? <span className="spinner" /> : <Icon name="upload" />}
      <span>Drop CSV exports here, or</span>
      <button type="button" className="link" onClick={() => input.current?.click()}>choose files</button>
      <input ref={input} type="file" accept=".csv" multiple hidden
        onChange={(event) => { onFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
    </div>
  );
}

function UploadStep({ month, meta, reload, toast }: { month: Month; meta: Meta; reload: () => void; toast: Toast }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [rejected, setRejected] = useState<{ name: string; reason: string }[]>([]);
  const [payout, setPayout] = useState(month.inputs.payout_local?.toString() ?? "");
  const [rate, setRate] = useState(month.inputs.fx_rate?.toString() ?? "");
  const [pools, setPools] = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(month.inputs.shorts_pool_usd).map(([key, value]) => [key, String(value)])),
  );

  const upload = async (vertical: string, files: File[]) => {
    if (!files.length) return;
    setBusy(vertical);
    try {
      const result = await api.upload(month.period, vertical, files);
      setRejected(result.rejected);
      if (result.saved.length) toast(`Added ${result.saved.length} ${result.saved.length === 1 ? "file" : "files"}.`);
      reload();
    } catch (problem) {
      toast((problem as Error).message, "bad");
    }
    setBusy(null);
  };

  const save = async () => {
    setBusy("inputs");
    try {
      await api.saveInputs(month.period, {
        payout_local: payout ? Number(payout) : null,
        fx_rate: rate ? Number(rate) : null,
        shorts_pool_usd: Object.fromEntries(Object.entries(pools).map(([key, value]) => [key, Number(value) || 0])),
      });
      toast("Saved.");
      reload();
    } catch (problem) {
      toast((problem as Error).message, "bad");
    }
    setBusy(null);
  };

  return (
    <>
      <p className="lede">
        Download the month's revenue reports from YouTube CMS and add them here, as they are. Nothing needs to be
        cleaned up first.
      </p>
      {rejected.length > 0 && (
        <div className="notice notice-warning">
          <Icon name="alert" />
          <div>
            <strong>Some files were not added</strong>
            {rejected.map((item) => <p key={item.name}>{item.name}: {item.reason}</p>)}
          </div>
        </div>
      )}
      <div className="grid-2">
        {meta.verticals.map((vertical) => (
          <section className="card" key={vertical.key}>
            <h3>{vertical.label}</h3>
            {(month.files[vertical.key] ?? []).length > 0 && (
              <ul className="files">
                {month.files[vertical.key].map((file) => (
                  <li key={file.name}>
                    <Icon name="file" />
                    <span className="file-name">
                      {file.name}
                      <small>{file.error ?? `${file.kind} · ${file.rows.toLocaleString()} rows · ${usd(file.revenue)}`}</small>
                    </span>
                    <button type="button" className="icon-btn" aria-label={`Remove ${file.name}`}
                      onClick={() => api.removeFile(month.period, vertical.key, file.name).then(reload)}>
                      <Icon name="x" size={16} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <DropZone busy={busy === vertical.key} onFiles={(files) => upload(vertical.key, files)} />
          </section>
        ))}
      </div>

      <section className="card">
        <h3>Figures for the month</h3>
        <p className="hint">These are not in the CMS exports, so they are entered once a month.</p>
        <div className="form-row">
          <label>
            Payout received ({meta.currency})
            <input type="number" min="0" value={payout} onChange={(event) => setPayout(event.target.value)} placeholder="e.g. 4250000" />
            <small>The exchange rate is worked out from this.</small>
          </label>
          <label>
            Or a fixed rate ({meta.currency} per USD)
            <input type="number" min="0" step="0.0001" value={rate} onChange={(event) => setRate(event.target.value)} placeholder="optional" />
            <small>Overrides the payout if set.</small>
          </label>
          {meta.verticals.filter((vertical) => vertical.shorts_mode === "pool").map((vertical) => (
            <label key={vertical.key}>
              {vertical.label} Shorts revenue (USD)
              <input type="number" min="0" step="0.01" value={pools[vertical.key] ?? ""} placeholder="0"
                onChange={(event) => setPools({ ...pools, [vertical.key]: event.target.value })} />
              <small>Shared between clients by their revenue.</small>
            </label>
          ))}
        </div>
        <Button kind="primary" onClick={save} busy={busy === "inputs"}>Save figures</Button>
      </section>
    </>
  );
}

/* ------------------------------------------------------------------ step 2 */

const NEW = "__new__";

function ClientPicker({ vertical, onMatch, label }: {
  vertical: Vertical;
  label: string;
  onMatch: (producer: string, commission?: number) => Promise<void>;
}) {
  const [choice, setChoice] = useState("");
  const [name, setName] = useState("");
  const [rate, setRate] = useState("20");
  const [busy, setBusy] = useState(false);
  const creating = choice === NEW;
  const ready = creating ? name.trim() !== "" && rate !== "" : choice !== "";
  return (
    <div className="picker">
      <select value={choice} onChange={(event) => setChoice(event.target.value)} aria-label="Client">
        <option value="">Choose a client…</option>
        {vertical.producers.map((producer) => <option key={producer.name}>{producer.name}</option>)}
        <option value={NEW}>+ New client…</option>
      </select>
      {creating && (
        <>
          <input placeholder="Client name" value={name} onChange={(event) => setName(event.target.value)} />
          <span className="suffix-input">
            <input type="number" min="0" max="100" value={rate} onChange={(event) => setRate(event.target.value)} aria-label="Our commission in percent" />
            <span>% ours</span>
          </span>
        </>
      )}
      <Button small kind="primary" disabled={!ready} busy={busy}
        onClick={async () => {
          setBusy(true);
          await onMatch(creating ? name.trim() : choice, creating ? Number(rate) / 100 : undefined);
          setBusy(false);
        }}>
        {label}
      </Button>
    </div>
  );
}

function MatchStep({ month, meta, reload, toast, reloadMeta }: { month: Month; meta: Meta; reload: () => void; toast: Toast; reloadMeta: () => void }) {
  const match = (vertical: string, kind: string, value: string, done: string) => async (producer: string, commission?: number) => {
    try {
      await api.match({ vertical, kind, value, producer, new_commission: commission });
      toast(done.replace("{client}", producer));
      reloadMeta();
      reload();
    } catch (problem) {
      toast((problem as Error).message, "bad");
    }
  };

  if (!month.totals.unidentified_count)
    return (
      <Empty title="Every asset has a client">
        {month.steps.upload === "done" ? "Nothing is left out of this month's payments." : "Add this month's files first."}
      </Empty>
    );

  return (
    <>
      <p className="lede">
        These assets earned money but we can't tell whose they are, so they are left out of the payments for now. Look
        them up in CMS, then tell us who they belong to. We'll remember for next month.
      </p>
      {month.verticals.filter((v) => v.unidentified.length).map((v) => {
        const vertical = meta.verticals.find((item) => item.key === v.key)!;
        return (
          <section className="card" key={v.key}>
            <h3>{v.label} <span className="count">{v.unidentified.length}</span></h3>
            {v.groups.length > 0 && (
              <div className="suggestions">
                <h4>Quick matches</h4>
                {v.groups.map((group) => (
                  <div className="match-row" key={group.kind + group.value}>
                    <div className="match-what">
                      <strong>{group.assets} {group.assets === 1 ? "asset" : "assets"} {group.kind === "label" ? "labelled" : "with a custom ID starting"} “{group.value}”</strong>
                      <small>{usd(group.revenue)} this month · also applies to future assets like these</small>
                    </div>
                    <ClientPicker vertical={vertical} label="Match all"
                      onMatch={match(v.key, group.kind, group.value, "Matched to {client}.")} />
                  </div>
                ))}
              </div>
            )}
            <h4>One by one</h4>
            {v.unidentified.map((asset) => {
              const isChannel = asset["Asset ID"] === "";
              return (
                <div className="match-row" key={asset["Asset ID"] + asset["Asset Channel ID"] + asset["Asset Title"]}>
                  <div className="match-what">
                    <strong>{asset["Asset Title"] || "Untitled"} <span className="money">{usd(asset["Partner Revenue"])}</span></strong>
                    <small>
                      {isChannel ? `Shorts channel ${asset["Asset Channel ID"]}` : asset["Asset ID"]}
                      {asset["Custom ID"] && <> · custom ID <code>{asset["Custom ID"]}</code></>}
                      {asset["Asset Labels"] && <> · label <code>{asset["Asset Labels"]}</code></>}
                      {!asset["Custom ID"] && !asset["Asset Labels"] && !isChannel && " · no custom ID or label"}
                    </small>
                  </div>
                  <ClientPicker vertical={vertical} label="Match"
                    onMatch={match(v.key, isChannel ? "channel" : "asset", isChannel ? asset["Asset Channel ID"] : asset["Asset ID"], "Matched to {client}.")} />
                </div>
              );
            })}
          </section>
        );
      })}
    </>
  );
}

/* ------------------------------------------------------------------ step 3 */

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="tile">
      <span>{label}</span>
      <strong>{value}</strong>
      {sub && <small>{sub}</small>}
    </div>
  );
}

export function SettlementTable({ lines, first }: { lines: SettlementLine[]; first: "Producer" | "Vertical" }) {
  const sum = (key: keyof SettlementLine) => lines.reduce((total, line) => total + (line[key] as number), 0);
  const cols: [keyof SettlementLine, string][] = [
    ["Revenue", "Earned"], ["Shorts Share", "Shorts"], ["Other Earnings", "Other"], ["Withholding Tax", "Tax withheld"],
    ["Net Revenue", "Net"], ["Network Commission", "Our commission"], ["Partner Payment", "We pay"],
  ];
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{first === "Producer" ? "Client" : "Vertical"}</th>
            {cols.map(([, title]) => <th key={title} className="num">{title}</th>)}
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line[first] as string}>
              <td>{line[first]}{line["Commission Rate"] !== undefined && <small> · {percent(line["Commission Rate"])}</small>}</td>
              {cols.map(([key]) => <td key={key} className="num">{usd(line[key] as number)}</td>)}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td>Total</td>
            {cols.map(([key]) => <td key={key} className="num">{usd(sum(key))}</td>)}
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function ReviewStep({ month }: { month: Month }) {
  const [tab, setTab] = useState("all");
  if (month.steps.upload !== "done") return <Empty title="Nothing to review yet">Add this month's files first.</Empty>;
  const { totals } = month;
  const conv = (value: number) => (month.fx_rate ? local(value * month.fx_rate, month.currency) : undefined);
  const lines = tab === "all" ? month.combined : month.verticals.find((v) => v.key === tab)!.settlement;
  return (
    <>
      <div className="tiles">
        <Tile label="Total revenue" value={usd(totals.revenue)} sub={conv(totals.revenue)} />
        <Tile label="We pay clients" value={usd(totals.payment)} sub={conv(totals.payment)} />
        <Tile label="Our commission" value={usd(totals.commission)} sub={conv(totals.commission)} />
        <Tile label="Tax withheld" value={usd(totals.withholding)} sub={month.fx_rate ? `Rate ${month.fx_rate.toFixed(4)} ${month.currency}/USD` : "No exchange rate yet"} />
      </div>
      {totals.unidentified > 0 && (
        <div className="notice notice-warning">
          <Icon name="alert" />
          <div><strong>{usd(totals.unidentified)} is not included yet</strong><p>It belongs to assets without a client. Match them in step 2.</p></div>
        </div>
      )}
      <div className="grid-2">
        <section className="card">
          <h3>Revenue by month</h3>
          <p className="hint">Matched revenue across all clients, in USD</p>
          {month.trend.length >= 2
            ? <TrendChart points={month.trend.map((point) => ({ period: point.period, value: point.revenue }))} />
            : <Empty title="One month so far">A trend appears from the second month.</Empty>}
        </section>
        <section className="card">
          <h3>What each client is paid</h3>
          <p className="hint">This month, in USD</p>
          <BarList items={month.combined.filter((line) => line["Partner Payment"] > 0).slice().sort((a, b) => b["Partner Payment"] - a["Partner Payment"]).slice(0, 8)
            .map((line) => ({ name: line.Producer, value: line["Partner Payment"] }))} total={totals.payment} />
        </section>
      </div>
      <section className="card">
        <div className="card-head">
          <h3>Settlement</h3>
          <div className="tabs" role="tablist">
            {[{ key: "all", label: "All clients" }, ...month.verticals].map((item) => (
              <button key={item.key} role="tab" type="button" aria-selected={tab === item.key} className={tab === item.key ? "tab tab-on" : "tab"} onClick={() => setTab(item.key)}>{item.label}</button>
            ))}
          </div>
        </div>
        <SettlementTable lines={lines} first="Producer" />
      </section>
    </>
  );
}

/* ------------------------------------------------------------------ step 4 */

function Password({ value }: { value: string }) {
  const [shown, setShown] = useState(false);
  const [copied, setCopied] = useState(false);
  return (
    <span className="password">
      <code>{shown ? value : "••••-••••-••••"}</code>
      <button type="button" className="icon-btn" onClick={() => setShown(!shown)} aria-label={shown ? "Hide password" : "Show password"}><Icon name="eye" size={16} /></button>
      <button type="button" className="icon-btn" aria-label="Copy password"
        onClick={() => navigator.clipboard?.writeText(value).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })}>
        <Icon name={copied ? "check" : "copy"} size={16} />
      </button>
    </span>
  );
}

const fileRank = (name: string) => (name.endsWith(".pdf") ? 0 : name.endsWith(".xlsx") ? 1 : 2);
const fileLabel = (name: string) => (name.endsWith(".pdf") ? "Report (PDF)" : name.endsWith(".xlsx") ? "Data (Excel)" : "Data (CSV, zipped)");

function SendStep({ month, meta, reload, toast }: { month: Month; meta: Meta; reload: () => void; toast: Toast }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ name: string; slug: string } | null>(null);
  const packages = month.packages;

  if (month.steps.upload !== "done") return <Empty title="Nothing to send yet">Add this month's files first.</Empty>;

  const generate = async () => {
    setBusy("generate");
    try {
      await api.generate(month.period);
      toast("Client packages are ready.");
      reload();
    } catch (problem) {
      toast((problem as Error).message, "bad");
    }
    setBusy(null);
  };
  const email = async (producer: string) => {
    setBusy(producer);
    try {
      const [result] = await api.deliver(month.period, producer);
      toast(result?.status === "sent" ? `Sent to ${result.recipient}.` : `Not sent: ${result?.status ?? "no files"}`, result?.status === "sent" ? "good" : "bad");
    } catch (problem) {
      toast((problem as Error).message, "bad");
    }
    setBusy(null);
  };

  return (
    <>
      <p className="lede">
        Each client gets a report and the data behind it. The files are password protected; a client's password stays
        the same every month, so share it once, separately from the files.
      </p>
      <div className="send-bar">
        <Button kind="primary" icon={packages ? "refresh" : "check"} busy={busy === "generate"} onClick={generate}>
          {packages ? "Prepare again" : "Prepare client packages"}
        </Button>
        {packages && (packages.stale
          ? <Status state="warning">Figures changed since {packages.generated_at}. Prepare again before sending.</Status>
          : <Status state="good">Prepared {packages.generated_at}</Status>)}
        {busy === "generate" && <span className="hint">Writing reports. This takes a few seconds…</span>}
      </div>
      {month.totals.unidentified_count > 0 && (
        <div className="notice notice-warning">
          <Icon name="alert" />
          <div><strong>{usd(month.totals.unidentified)} is still unmatched</strong><p>Reports prepared now will not include it.</p></div>
        </div>
      )}

      <section className="card">
        {month.combined.map((line) => {
          const slug = meta.verticals.flatMap((v) => v.producers).find((p) => p.name === line.Producer)?.slug ?? "";
          const pack = packages?.clients.find((client) => client.producer === line.Producer);
          return (
            <div className="send-row" key={line.Producer}>
              <Avatar name={line.Producer} />
              <div className="send-who">
                <strong>{line.Producer}</strong>
                <small>{usd(line["Partner Payment"])}{pack?.email ? ` · ${pack.email}` : ""}</small>
                {pack && !packages?.stale && <span className="hint">Password <Password value={pack.password} /></span>}
              </div>
              <div className="send-actions">
                <Button small icon="eye" onClick={() => setPreview({ name: line.Producer, slug })}>Preview report</Button>
                {pack && !packages?.stale && pack.files.slice().sort((a, b) => fileRank(a) - fileRank(b)).map((file) => (
                  <a key={file} className="btn btn-ghost btn-small" href={api.downloadUrl(month.period, pack.slug, file)}><Icon name="download" size={15} />{fileLabel(file)}</a>
                ))}
                {pack && !packages?.stale && !meta.demo && pack.email && (
                  <Button small icon="mail" busy={busy === line.Producer} onClick={() => email(line.Producer)}>E-mail</Button>
                )}
              </div>
            </div>
          );
        })}
      </section>
      {packages && !packages.stale && (
        <p className="hint">
          For your own records: <a href={api.downloadUrl(month.period, "internal", packages.internal.file)}>the full settlement workbook</a>{" "}
          (password <Password value={packages.internal.password} />).
          {meta.demo && " The demo never sends e-mail; download the files to see them."}
        </p>
      )}
      {preview && (
        <Modal wide title={`${preview.name} · ${month.label}`} onClose={() => setPreview(null)}>
          <iframe className="pdf" title="Report preview" src={api.previewUrl(month.period, preview.slug)} />
        </Modal>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ page */

export function MonthPage({ meta, period, step, toast, reloadMeta }: { meta: Meta; period: string; step?: string; toast: Toast; reloadMeta: () => void }) {
  const { data: month, error, reload } = useData(() => api.month(period), [period]);
  // The open step lives in the address (#/month/review), so it can be linked to and survives a reload.
  const picked = ORDER.includes(step as StepKey) ? (step as StepKey) : null;
  const setPicked = (next: StepKey) => { window.location.hash = `#/month/${next}`; };

  if (error) return <Empty title="This month could not be opened">{error}</Empty>;
  if (!month) return <Loading />;

  const lead = headline(month);
  const active = picked ?? lead.go;
  const panels: Record<StepKey, ReactNode> = {
    upload: <UploadStep key={period} month={month} meta={meta} reload={reload} toast={toast} />,
    match: <MatchStep month={month} meta={meta} reload={reload} toast={toast} reloadMeta={reloadMeta} />,
    review: <ReviewStep month={month} />,
    send: <SendStep month={month} meta={meta} reload={reload} toast={toast} />,
  };
  const next = ORDER[ORDER.indexOf(active) + 1];

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{month.label}</h1>
          <p className="headline">{lead.text}</p>
        </div>
        {active !== lead.go && <Button kind="primary" onClick={() => setPicked(lead.go)}>{lead.action}</Button>}
      </header>
      <Stepper month={month} active={active} onPick={setPicked} />
      <div className="panel">{panels[active]}</div>
      {next && (
        <div className="next">
          <Button onClick={() => setPicked(next)}>Next: {TITLES[next]} →</Button>
        </div>
      )}
    </>
  );
}
