import { useState } from "react";
import { api, Meta, TestInfo } from "./api";
import { BarList, Sparkline, TrendChart } from "./charts";
import { SettlementTable } from "./Month";
import { Avatar, Button, Empty, Icon, Loading, Modal, percent, Status, usd, useData } from "./ui";

type Toast = (message: string, kind?: "good" | "bad") => void;

function Delta({ now, before }: { now: number; before: number | null }) {
  if (before === null) return <span className="delta">First month</span>;
  if (before === 0) return <span className="delta">{now ? "Nothing last month" : "Kept by the network"}</span>;
  const change = (now - before) / before;
  if (Math.abs(change) < 0.0005) return <span className="delta">No change</span>;
  return (
    <span className={`delta ${change > 0 ? "delta-up" : "delta-down"}`}>
      <Icon name={change > 0 ? "up" : "down"} size={14} />
      {percent(Math.abs(change))} vs last month
    </span>
  );
}

export function ClientsPage({ period, label }: { period: string; label: string }) {
  const { data, error } = useData(() => api.clients(period), [period]);
  if (error) return <Empty title="Clients could not be loaded">{error}</Empty>;
  if (!data) return <Loading />;
  return (
    <>
      <header className="page-head">
        <div>
          <h1>Clients</h1>
          <p className="headline">{data.length} clients earned revenue in {label}.</p>
        </div>
      </header>
      {data.length === 0 && <Empty title="No clients yet">Add this month's files and match the assets first.</Empty>}
      <div className="cards">
        {data.map((client) => (
          <a className="card client-card" key={client.slug} href={`#/clients/${client.slug}`}>
            <div className="client-top">
              <Avatar name={client.producer} />
              <div>
                <strong>{client.producer}</strong>
                <small>{client.verticals.join(" · ")}</small>
              </div>
            </div>
            <div className="client-pay">
              <div>
                <small>We pay</small>
                <strong>{usd(client.payment)}</strong>
              </div>
              <Sparkline values={client.history.map((point) => point.payment)} />
            </div>
            <Delta now={client.payment} before={client.previous_payment} />
          </a>
        ))}
      </div>
    </>
  );
}

export function ClientPage({ period, label, slug, meta }: { period: string; label: string; slug: string; meta: Meta }) {
  const { data, error } = useData(() => api.client(period, slug), [period, slug]);
  const [preview, setPreview] = useState(false);
  if (error)
    return (
      <>
        <a className="back" href="#/clients"><Icon name="back" size={16} />Clients</a>
        <Empty title="Nothing to show">{error}</Empty>
      </>
    );
  if (!data) return <Loading />;
  const rules = meta.verticals
    .map((vertical) => ({ vertical: vertical.label, producer: vertical.producers.find((p) => p.slug === slug) }))
    .filter((entry) => entry.producer);
  return (
    <>
      <a className="back" href="#/clients"><Icon name="back" size={16} />Clients</a>
      <header className="page-head">
        <div className="client-top">
          <Avatar name={data.producer} />
          <div>
            <h1>{data.producer}</h1>
            <p className="headline">{label} · we pay {usd(data.totals["Partner Payment"])}{data.email ? ` · ${data.email}` : ""}</p>
          </div>
        </div>
        <Button kind="primary" icon="eye" onClick={() => setPreview(true)}>Preview report</Button>
      </header>
      <section className="card">
        <h3>Statement</h3>
        <SettlementTable lines={data.statement} first="Vertical" />
      </section>
      <div className="grid-2">
        <section className="card">
          <h3>Revenue by month</h3>
          <p className="hint">Before Shorts and deductions, in USD</p>
          {data.history.length >= 2 ? <TrendChart points={data.history} /> : <Empty title="One month so far" />}
        </section>
        <section className="card">
          <h3>Where the revenue came from</h3>
          <p className="hint">Top countries this month, in USD</p>
          <BarList items={data.countries.slice(0, 8)} total={data.totals.Revenue} />
        </section>
      </div>
      <div className="grid-2">
        <section className="card">
          <h3>Top earning assets</h3>
          <p className="hint">This month, in USD</p>
          <BarList items={data.top_assets} total={data.totals.Revenue} />
        </section>
        <section className="card">
          <h3>How we recognise their assets</h3>
          <p className="hint">Added automatically when you match assets in step 2</p>
          {rules.map(({ vertical, producer }) => (
            <div className="rules" key={vertical}>
              <h4>{vertical} · our commission {percent(producer!.commission)}</h4>
              <p><span>Labels</span>{producer!.labels.length ? producer!.labels.map((v) => <code key={v}>{v}</code>) : <em>none</em>}</p>
              <p><span>Custom IDs containing</span>{producer!.patterns.length ? producer!.patterns.map((v) => <code key={v}>{v}</code>) : <em>none</em>}</p>
              <p><span>Channels</span>{producer!.channels.length ? producer!.channels.map((v) => <code key={v}>{v}</code>) : <em>none</em>}</p>
            </div>
          ))}
        </section>
      </div>
      {preview && (
        <Modal wide title={`${data.producer} · ${label}`} onClose={() => setPreview(false)}>
          <iframe className="pdf" title="Report preview" src={api.previewUrl(period, slug)} />
        </Modal>
      )}
    </>
  );
}

const count = (value: unknown) => (value === null || value === undefined ? "hidden" : Number(value).toLocaleString("en-US"));

export function ChannelsPage({ toast, meta }: { toast: Toast; meta: Meta }) {
  const { data, error, setData } = useData(() => api.backups(), []);
  const [busy, setBusy] = useState(false);
  if (error) return <Empty title="Channels could not be loaded">{error}</Empty>;
  if (!data) return <Loading />;
  const take = async () => {
    setBusy(true);
    try {
      setData(await api.takeBackup());
      toast("Snapshot saved.");
    } catch (problem) {
      toast((problem as Error).message, "bad");
    }
    setBusy(false);
  };
  const latest = data.latest;
  return (
    <>
      <header className="page-head">
        <div>
          <h1>Channels</h1>
          <p className="headline">
            {latest ? `Last snapshot ${latest.taken.replace(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2}).*/, "$1-$2-$3 $4:$5")} · ${data.snapshots.length} kept.` : "No snapshot yet."}
          </p>
        </div>
        <Button kind="primary" icon="refresh" busy={busy} onClick={take}>Take a snapshot</Button>
      </header>
      <p className="lede">
        A snapshot records every channel's name, handle, description and counts. If a channel is ever hijacked or
        removed, this is the proof of ownership YouTube asks for, and the list of changes below is the early warning.
        {!data.live && (meta.demo ? " The demo uses made-up channels." : " No YouTube API key is set, so made-up channels are shown.")}
      </p>
      {latest && (
        <>
          <section className="card">
            <h3>Changes since the previous snapshot</h3>
            {latest.changes.length === 0 ? <Status state="good">Nothing changed</Status> : (
              <ul className="changes">
                {latest.changes.map((change, index) => (
                  <li key={index}>
                    <Icon name="alert" size={16} />
                    <span><strong>{change.Change}</strong> · {change["Channel ID"]}{change.Before && <> · was “{change.Before}”</>}{change.After && <> · now “{change.After}”</>}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="card">
            <h3>{latest.channels.length} channels</h3>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Channel</th><th>Client</th><th>Vertical</th><th className="num">Subscribers</th><th className="num">Views</th><th className="num">Videos</th></tr></thead>
                <tbody>
                  {latest.channels.map((channel) => (
                    <tr key={String(channel["Channel ID"])}>
                      <td>{channel.Found ? String(channel.Title) : <Status state="warning">Not found</Status>}<small> {String(channel["Custom URL"] ?? "")}</small></td>
                      <td>{String(channel.Producer)}</td>
                      <td>{String(channel.Vertical)}</td>
                      <td className="num">{count(channel["Subscriber Count"])}</td>
                      <td className="num">{count(channel["View Count"])}</td>
                      <td className="num">{count(channel["Video Count"])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}

/** Shows the alert as it arrives in a chat, and sends it to a real phone when the instance is connected. */
function TryAlert({ info, meta, toast, onSent }: { info: TestInfo; meta: Meta; toast: Toast; onSent: (info: TestInfo) => void }) {
  const [channel, setChannel] = useState<"telegram" | "whatsapp">("telegram");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const telegram = info.channels.telegram;
  const whatsapp = info.channels.whatsapp;
  const current = channel === "telegram" ? telegram : whatsapp;
  const name = channel === "telegram" ? "Telegram" : "WhatsApp";
  const needsDestination = meta.demo || !current.has_default;
  const spent = info.remaining === 0;
  const [first, ...rest] = info.text.split("\n");

  const send = async () => {
    setBusy(true);
    try {
      onSent(await api.testAlert(channel, to));
      toast(`Sent. Check ${name}.`);
    } catch (problem) {
      toast((problem as Error).message, "bad");
    }
    setBusy(false);
  };

  return (
    <section className="card try-alert">
      <div className="try-text">
        <div className="card-head">
          <h3>Try an alert on your phone</h3>
          <div className="tabs" role="tablist">
            {(["telegram", "whatsapp"] as const).map((key) => (
              <button key={key} role="tab" type="button" aria-selected={channel === key} className={channel === key ? "tab tab-on" : "tab"} onClick={() => { setChannel(key); setTo(""); }}>
                {key === "telegram" ? "Telegram" : "WhatsApp"}
              </button>
            ))}
          </div>
        </div>
        <p className="hint">This is the message a manager receives when a new conflict appears. The example is made up.</p>

        {!current.available ? (
          <p className="try-note">
            This instance is not connected to {name}, so only the preview is shown.{" "}
            {channel === "telegram"
              ? "To connect it, create a bot with @BotFather and set TELEGRAM_BOT_TOKEN."
              : "To connect it, set the TWILIO_* settings for a WhatsApp sender."}
          </p>
        ) : (
          <>
            {channel === "telegram" ? (
              <ol className="try-steps">
                <li>Open {telegram.bot ? <a href={`https://t.me/${telegram.bot}`} target="_blank" rel="noreferrer">@{telegram.bot}</a> : "the alert bot"} in Telegram and press <strong>Start</strong>.</li>
                <li>Send <code>/start</code> to <a href="https://t.me/userinfobot" target="_blank" rel="noreferrer">@userinfobot</a> to see your chat ID.</li>
                <li>Enter it here.</li>
              </ol>
            ) : (
              <ol className="try-steps">
                <li>
                  {whatsapp.join_code
                    ? <>Send <code>{whatsapp.join_code}</code> to <strong>{whatsapp.sender}</strong> on WhatsApp to allow messages.</>
                    : <>Your number must have messaged <strong>{whatsapp.sender}</strong> on WhatsApp first.</>}
                </li>
                <li>Enter your number in international format.</li>
              </ol>
            )}
            <div className="picker">
              {needsDestination || to ? null : <span className="hint">Goes to the configured {channel === "telegram" ? "chat" : "number"}, or enter another:</span>}
              <input value={to} onChange={(event) => setTo(event.target.value)} inputMode={channel === "telegram" ? "numeric" : "tel"}
                placeholder={channel === "telegram" ? "Chat ID, e.g. 123456789" : "+905551234567"} aria-label={channel === "telegram" ? "Telegram chat ID" : "WhatsApp number"} />
              <Button kind="primary" icon="bell" busy={busy} disabled={spent || (needsDestination && !to.trim())} onClick={send}>Send test alert</Button>
            </div>
            {info.remaining !== null && (
              <p className="hint">{spent ? "You have used this session's test alerts." : `${info.remaining} test ${info.remaining === 1 ? "alert" : "alerts"} left in this demo session. The message is fixed and your ${channel === "telegram" ? "chat ID" : "number"} is not stored.`}</p>
            )}
          </>
        )}
      </div>
      <div className="phone" aria-label={`Preview of the ${name} message`}>
        <div className="phone-bar">{name} · CMS Reporter alerts</div>
        <div className="bubble">
          <strong>{first}</strong>
          {rest.map((line) => <span key={line}>{line}</span>)}
          <small>now</small>
        </div>
      </div>
    </section>
  );
}

export function AlertsPage({ toast, meta }: { toast: Toast; meta: Meta }) {
  const { data, error, setData } = useData(() => api.conflicts(), []);
  const [busy, setBusy] = useState(false);
  if (error) return <Empty title="Alerts could not be loaded">{error}</Empty>;
  if (!data) return <Loading />;
  const check = async () => {
    setBusy(true);
    try {
      const result = await api.checkConflicts();
      setData(result);
      toast(result.new ? `${result.new} new ${result.new === 1 ? "conflict" : "conflicts"}.` : "Nothing new.");
    } catch (problem) {
      toast((problem as Error).message, "bad");
    }
    setBusy(false);
  };
  return (
    <>
      <header className="page-head">
        <div>
          <h1>Alerts</h1>
          <p className="headline">{data.conflicts.length ? `${data.conflicts.length} copyright and licence issues on record.` : "No copyright or licence issues on record."}</p>
        </div>
        <Button kind="primary" icon="refresh" busy={busy} onClick={check}>Check now</Button>
      </header>
      <p className="lede">
        Ownership conflicts, disputed claims and takedowns have deadlines and are easy to miss in e-mail. New ones are
        announced once, to: {data.channels.join(", ")}.
        {meta.demo && " In the demo, checking loads three made-up issues and sends nothing."}
      </p>
      <TryAlert info={data.test} meta={meta} toast={toast} onSent={(test) => setData({ ...data, test })} />
      {data.conflicts.length === 0 && <Empty title="All clear">Press “Check now” to look for new issues.</Empty>}
      <div className="cards">
        {data.conflicts.map((conflict) => (
          <section className="card conflict" key={conflict.id}>
            <Status state="warning">{conflict.kind}</Status>
            <strong>{conflict.title || conflict.asset_id}</strong>
            <dl>
              {conflict.other_party && <><dt>Other party</dt><dd>{conflict.other_party}</dd></>}
              {conflict.territories && <><dt>Where</dt><dd>{conflict.territories}</dd></>}
              {conflict.status && <><dt>Status</dt><dd>{conflict.status}</dd></>}
              {conflict.due && <><dt>Act before</dt><dd>{conflict.due}</dd></>}
              <dt>Announced</dt><dd>{conflict.notified_at}</dd>
            </dl>
          </section>
        ))}
      </div>
    </>
  );
}
